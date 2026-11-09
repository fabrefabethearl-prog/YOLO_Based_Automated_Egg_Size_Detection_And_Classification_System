"""Dataset folder summary and labelled-image upload."""

from __future__ import annotations

import uuid
from pathlib import Path

import cv2
import numpy as np
import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator
from starlette.formparsers import MultiPartException

from backend import db as database
from backend.auth import get_current_user
from backend.models import User
from backend.paths import DATA_YAML, IMAGE_SUFFIXES, dataset_dir
from backend.services.audit import add_log
from backend.services.annotations import (
    AnnotationRejected,
    annotation_counts,
    delete_annotation,
    drop_missing,
    get_annotation,
    image_path,
    list_images,
    save_annotation,
    spec_for,
)
from backend.services.dataset_prep import DatasetRejected, clear_incoming, incoming_status, prepare_incoming, save_class
from backend.services.settings_store import get_settings
from backend.services.dataset_train import TrainingRejected, start_training, training_status
from backend.services.images import MAX_BYTES, ImageError, decode_image, encode_jpeg
from backend.services.egg_geometry import EggMeasureError, measure_egg_at_point, measure_eggs
from backend.services.annotation_labels import FILENAME_LABELS, filename_reference_label

router = APIRouter(prefix="/api/dataset", tags=["dataset"], dependencies=[Depends(get_current_user)])
SPLITS = ("train", "val", "test")


def class_names() -> dict[int, str]:
    from backend.db import DEFAULT_CATEGORIES

    fallback = {index: str(item["name"]) for index, item in enumerate(DEFAULT_CATEGORIES)}
    if not DATA_YAML.is_file():
        return fallback
    try:
        data = yaml.safe_load(DATA_YAML.read_text(encoding="utf-8")) or {}
    except Exception:
        return fallback
    raw = data.get("names", fallback)
    if isinstance(raw, dict):
        parsed = {int(key): str(value) for key, value in raw.items()}
        return parsed or fallback
    if isinstance(raw, list):
        return {index: str(value) for index, value in enumerate(raw)} or fallback
    return fallback


def _images(split: str) -> list[Path]:
    folder = dataset_dir() / "images" / split
    if not folder.is_dir():
        return []
    return sorted(path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES)


@router.get("")
def dataset_summary():
    names = class_names()
    splits = {split: len(_images(split)) for split in SPLITS}
    image_hits = {class_id: 0 for class_id in names}
    object_hits = {class_id: 0 for class_id in names}
    labeled = 0
    unlabeled = 0
    for split in SPLITS:
        for image in _images(split):
            label_path = dataset_dir() / "labels" / split / f"{image.stem}.txt"
            if not label_path.is_file():
                unlabeled += 1
                continue
            classes_in_image = set()
            text = label_path.read_text(encoding="utf-8", errors="ignore")
            has_box = False
            for line in text.splitlines():
                parts = line.split()
                if len(parts) != 5:
                    continue
                try:
                    class_id = int(float(parts[0]))
                except ValueError:
                    continue
                has_box = True
                classes_in_image.add(class_id)
                object_hits[class_id] = object_hits.get(class_id, 0) + 1
                image_hits.setdefault(class_id, 0)
            if has_box:
                labeled += 1
                for class_id in classes_in_image:
                    image_hits[class_id] = image_hits.get(class_id, 0) + 1
            else:
                unlabeled += 1

    known = set(names) | set(image_hits) | set(object_hits)
    per_class = []
    for class_id in sorted(known):
        per_class.append(
            {
                "id": class_id,
                "name": names.get(class_id, f"class {class_id}"),
                "images": image_hits.get(class_id, 0),
                "objects": object_hits.get(class_id, 0),
            }
        )
    return {
        "total_images": sum(splits.values()),
        "labeled_images": labeled,
        "unlabeled_images": unlabeled,
        "splits": splits,
        "per_class": per_class,
    }


def _validate_label(text: str) -> str:
    lines = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 5:
            raise ImageError(400, "Each label line must be: class x y w h.")
        try:
            class_id = int(float(parts[0]))
            numbers = [float(part) for part in parts[1:]]
        except ValueError as exc:
            raise ImageError(400, "Label values must be numbers.") from exc
        if class_id not in class_names() or any(number < 0 or number > 1 for number in numbers):
            raise ImageError(
                400,
                "Each label line must use class id 0 Small, 1 Medium, 2 Large, 3 Extra Large, or 4 Jumbo, "
                "and box values between 0 and 1.",
            )
        lines.append(f"{class_id} " + " ".join(f"{number:.6f}" for number in numbers))
    if not lines:
        raise ImageError(400, "Label file is empty.")
    return "\n".join(lines) + "\n"


@router.post("/upload")
async def upload_sample(request: Request, user: User = Depends(get_current_user)):
    try:
        async with request.form(max_part_size=11 * 1024 * 1024, max_files=5, max_fields=10) as form:
            image = form.get("image")
            label = form.get("label")
            split = str(form.get("split") or "")
            if image is None or not hasattr(image, "read") or label is None or not hasattr(label, "read"):
                raise HTTPException(status_code=400, detail="Upload both an image and a YOLO label file.")
            raw = await image.read()
            label_bytes = await label.read()
            filename = getattr(image, "filename", "") or ""
    except HTTPException:
        raise
    except MultiPartException as exc:
        raise HTTPException(status_code=413, detail="Image must be 10 MB or smaller.") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Could not read the upload.") from exc

    if split not in SPLITS:
        raise HTTPException(status_code=400, detail="Split must be train, val, or test.")
    if len(raw) > MAX_BYTES or len(label_bytes) > 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image must be 10 MB or smaller.")
    try:
        decode_image(raw)
        label_text = _validate_label(label_bytes.decode("utf-8", errors="replace"))
    except ImageError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    suffix = Path(filename).suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        suffix = ".jpg"
        array = np.frombuffer(raw, dtype=np.uint8)
        decoded = cv2.imdecode(array, cv2.IMREAD_COLOR)
        raw = encode_jpeg(decoded)

    stem = uuid.uuid4().hex
    image_path = dataset_dir() / "images" / split / f"{stem}{suffix}"
    label_path = dataset_dir() / "labels" / split / f"{stem}.txt"
    image_path.write_bytes(raw)
    label_path.write_text(label_text, encoding="utf-8")
    db = database.SessionLocal()
    try:
        add_log(db, "INFO", f"{user.email} uploaded a labelled image to Dataset/{split}.")
        db.commit()
    finally:
        db.close()
    return {"ok": True, "split": split, "filename": image_path.name}


class PrepareBody(BaseModel):
    train_pct: int = Field(default=70, ge=1, le=98)
    val_pct: int = Field(default=20, ge=1, le=98)
    test_pct: int = Field(default=10, ge=0, le=98)
    seed: int = Field(default=42, ge=0, le=2_147_483_647)


class OvalMark(BaseModel):
    start_x: float
    start_y: float
    end_x: float
    end_y: float
    length_mm: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    width_mm: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    pixels_per_mm: float | None = Field(default=None, gt=0)
    calibration: str = "manual"
    geometry: dict | None = None


class AnnotationPoint(BaseModel):
    x: float = Field(ge=0)
    y: float = Field(ge=0)


class AnnotationBody(BaseModel):
    ovals: list[OvalMark] = Field(min_length=1, max_length=40)
    reference_class: str | None = None
    reference_label_source: str | None = None
    reviewed: bool = False

    @model_validator(mode="before")
    @classmethod
    def accept_one_oval(cls, data):
        if isinstance(data, dict) and "start_x" in data and "ovals" not in data:
            geometry_fields = (
                "center_x",
                "center_y",
                "angle_deg",
                "major_px",
                "minor_px",
                "length_px",
                "width_px",
                "area_px",
                "perimeter_px",
                "contour",
                "cross_sections",
            )
            geometry = {key: data[key] for key in geometry_fields if key in data}
            oval = dict(data)
            if geometry:
                oval["geometry"] = geometry
            normalized = {"ovals": [oval]}
            for field in ("reference_class", "reference_label_source", "reviewed"):
                if field in data:
                    normalized[field] = data[field]
            return normalized
        return data





@router.get("/incoming")
def read_incoming():
    body = incoming_status()
    body["annotated"] = annotation_counts()
    return body


@router.post("/class")
async def upload_class_folder(request: Request, user: User = Depends(get_current_user)):
    entries: list[tuple[str, bytes]] = []
    try:
        async with request.form(max_part_size=11 * 1024 * 1024, max_files=5000, max_fields=20) as form:
            class_name = str(form.get("class_name") or "")
            folder_name = str(form.get("folder_name") or "")
            uploads = [item for item in form.getlist("files") if hasattr(item, "read")]
            if not uploads:
                raise HTTPException(status_code=400, detail="Choose a folder of images.")
            for item in uploads:
                entries.append((getattr(item, "filename", "") or "", await item.read()))
    except HTTPException:
        raise
    except MultiPartException as exc:
        raise HTTPException(status_code=413, detail="Each image must be 10 MB or smaller.") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Could not read the class folder.") from exc
    try:
        result = save_class(class_name, folder_name, entries)
    except DatasetRejected as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    if not result.get("errors"):
        try:
            drop_missing(spec_for(result["class_key"]))
        except AnnotationRejected:
            pass
    if result.get("class_valid"):
        db = database.SessionLocal()
        try:
            add_log(db, "INFO", f"{user.email} uploaded a class folder: {result['message']}")
            db.commit()
        finally:
            db.close()
    return result


@router.delete("/class/{class_name}")
def delete_class_images(class_name: str, user: User = Depends(get_current_user)):
    try:
        result = clear_incoming(class_name)
    except DatasetRejected as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    result["annotated"] = annotation_counts()
    db = database.SessionLocal()
    try:
        add_log(db, "INFO", f"{user.email} deleted class images: {result['message']}")
        db.commit()
    finally:
        db.close()
    return result


@router.post("/prepare")
def prepare_dataset(body: PrepareBody, user: User = Depends(get_current_user)):
    try:
        result = prepare_incoming(
            train_pct=body.train_pct,
            val_pct=body.val_pct,
            test_pct=body.test_pct,
            seed=body.seed,
        )
    except DatasetRejected as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    if result["ready"]:
        db = database.SessionLocal()
        try:
            add_log(
                db,
                "INFO",
                f"{user.email} prepared a YOLO detection dataset with {result['total_images']} images.",
            )
            db.commit()
        finally:
            db.close()
    return result


@router.get("/train")
def read_training():
    return training_status()


@router.post("/train")
def begin_training(user: User = Depends(get_current_user)):
    try:
        result = start_training()
    except TrainingRejected as exc:
        status = 409 if "already running" in exc.message else 400
        raise HTTPException(status_code=status, detail=exc.message) from exc
    db = database.SessionLocal()
    try:
        add_log(db, "INFO", f"{user.email} started YOLO training from the prepared dataset.")
        db.commit()
    finally:
        db.close()
    return result


def _annotation_spec(class_name: str):
    try:
        return spec_for(class_name)
    except AnnotationRejected as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc


@router.get("/annotate/{class_name}")
def list_class_annotations(class_name: str):
    return list_images(_annotation_spec(class_name))


@router.get("/annotate/{class_name}/{filename}")
def annotation_image(class_name: str, filename: str):
    try:
        path = image_path(_annotation_spec(class_name), filename)
    except AnnotationRejected as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    return FileResponse(path)


@router.post("/annotate/{class_name}/{filename}/auto")
def auto_annotate(class_name: str, filename: str):
    spec = _annotation_spec(class_name)
    try:
        path = image_path(spec, filename)
        image = decode_image(path.read_bytes())
    except AnnotationRejected as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    except ImageError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    db = database.SessionLocal()
    try:
        pixels_per_mm = get_settings(db)["pixels_per_mm"]
    finally:
        db.close()
    try:
        result = measure_eggs(image, pixels_per_mm=pixels_per_mm)
    except EggMeasureError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc
    reference_class = filename_reference_label(path.name)
    result["filename"] = path.name
    result["dataset_class"] = spec["name"]
    result["reference_label"] = {
        "class_name": reference_class,
        "source": "filename" if reference_class else None,
        "requires_selection": reference_class is None,
    }
    result["model_prediction"] = None
    if len(result.get("eggs", [])) > 1:
        result.setdefault("warnings", []).append(
            "Multiple egg contours were detected. Review each candidate before saving."
        )
    result["review_required"] = True
    return result


@router.post("/annotate/{class_name}/{filename}/guide")
def guide_annotation(class_name: str, filename: str, body: AnnotationPoint):
    spec = _annotation_spec(class_name)
    try:
        path = image_path(spec, filename)
        image = decode_image(path.read_bytes())
    except AnnotationRejected as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    except ImageError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    db = database.SessionLocal()
    try:
        pixels_per_mm = get_settings(db)["pixels_per_mm"]
    finally:
        db.close()
    try:
        result = measure_egg_at_point(image, body.x, body.y, pixels_per_mm=pixels_per_mm)
    except EggMeasureError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from exc
    reference_class = filename_reference_label(path.name)
    result["filename"] = path.name
    result["dataset_class"] = spec["name"]
    result["reference_label"] = {
        "class_name": reference_class,
        "source": "filename" if reference_class else None,
        "requires_selection": reference_class is None,
    }
    result["model_prediction"] = None
    result["review_required"] = True
    return result


@router.get("/annotate/{class_name}/{filename}/mark")
def read_mark(class_name: str, filename: str):
    try:
        row = get_annotation(_annotation_spec(class_name), filename)
    except AnnotationRejected as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="This image has no saved length.")
    return row


@router.put("/annotate/{class_name}/{filename}/mark")
def write_mark(
    class_name: str,
    filename: str,
    body: AnnotationBody,
    user: User = Depends(get_current_user),
):
    spec = _annotation_spec(class_name)
    db = database.SessionLocal()
    try:
        pixels_per_mm = get_settings(db)["pixels_per_mm"]
    finally:
        db.close()
    try:
        result = save_annotation(spec, filename, body.model_dump(), pixels_per_mm)
    except AnnotationRejected as exc:
        status = 404 if "not found" in exc.message else 400
        raise HTTPException(status_code=status, detail=exc.message) from exc
    db = database.SessionLocal()
    try:
        add_log(db, "INFO", f"{user.email} saved an annotation for Dataset/{class_name}/{filename}.")
        db.commit()
    finally:
        db.close()
    return result


@router.delete("/annotate/{class_name}/{filename}/mark")
def remove_mark(class_name: str, filename: str, user: User = Depends(get_current_user)):
    try:
        delete_annotation(_annotation_spec(class_name), filename)
    except AnnotationRejected as exc:
        status = 404 if "no saved" in exc.message or "not found" in exc.message else 400
        raise HTTPException(status_code=status, detail=exc.message) from exc
    db = database.SessionLocal()
    try:
        add_log(db, "INFO", f"{user.email} deleted an annotation for Dataset/{class_name}/{filename}.")
        db.commit()
    finally:
        db.close()
    return {"deleted": True}
