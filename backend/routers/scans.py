"""Live scan and saved records."""

from __future__ import annotations

import csv
import io
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import cv2
from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import String, case, cast, func, or_
from sqlalchemy.orm import Session
from starlette.formparsers import MultiPartException

from backend.auth import get_current_user
from backend.db import get_db
from backend.models import Scan, User
from backend.paths import scans_dir
from backend.schemas import ScanPatch
from backend.services.audit import add_log
from backend.services.detector import annotate, detector
from backend.services.annotation import annotate_uploaded_image, format_annotation_response
from backend.services.images import ImageError, decode_base64_image, decode_image, encode_jpeg, to_data_url
from backend.services.egg_geometry import EggMeasureError, measure_eggs
from backend.services.infer import detect_frame, detect_scaled
from backend.services.model_info import active_model
from backend.services.serialize import isoformat, scan_dict
from backend.services.settings_store import CLASS_MESSAGE, SIZE_NAMES, get_categories, get_settings
from backend.services.length_model import class_for_measured_size
from backend.services.size import OUT_OF_RANGE, area_mm2, assign_class, box_to_mm

router = APIRouter(tags=["scans"], dependencies=[Depends(get_current_user)])


def _optional_range(value, label: str, low: float, high: float, *, allow_zero: bool = False) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"{label} must be a number.") from exc
    if number != number or number > high or (number < low if allow_zero else number <= low):
        raise HTTPException(status_code=400, detail=f"{label} is out of range.")
    return number


class _Upload:
    def __init__(self, raw: bytes, session_id: str | None, source: dict):
        self.raw = raw
        self.session_id = session_id
        self.confidence = _optional_range(source.get("confidence"), "Confidence", 0, 1, allow_zero=True)
        self.length_mm = _optional_range(source.get("length_mm"), "Length", 0, 500)
        self.width_mm = _optional_range(source.get("width_mm"), "Width", 0, 500)
        supplied = source.get("predicted_class")
        if supplied not in (None, "") and str(supplied).strip() not in SIZE_NAMES:
            raise HTTPException(status_code=422, detail=CLASS_MESSAGE)


_SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")

PIPELINE = (
    ("placed", "Egg placed"),
    ("captured", "Image captured"),
    ("detect", "YOLO detects"),
    ("estimate", "Size estimated"),
    ("class", "Class assigned"),
    ("shown", "Result displayed"),
)


def _session_id(value) -> str | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if not _SESSION_ID.fullmatch(text):
        raise HTTPException(
            status_code=400,
            detail="Session id must be 1–64 letters, numbers, hyphens, or underscores.",
        )
    return text


def _steps(done_through: str, *, failed: str | None = None, reason: str | None = None, inference_ms: float | None = None) -> list[dict]:
    order = [item[0] for item in PIPELINE]
    done_index = order.index(done_through)
    fail_index = order.index(failed) if failed else None
    steps = []
    for index, (key, label) in enumerate(PIPELINE):
        if fail_index is not None and index == fail_index:
            status = "fail"
        elif index <= done_index and fail_index is None:
            status = "done"
        elif fail_index is not None and index < fail_index:
            status = "done"
        else:
            status = "pending"
        item = {"id": key, "label": label, "status": status}
        if key == "detect" and inference_ms is not None:
            item["ms"] = round(float(inference_ms), 2)
        if status == "fail" and reason:
            item["reason"] = reason
        steps.append(item)
    return steps


async def _read_upload(request: Request) -> _Upload:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Could not read the JSON body.") from exc
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="Attach an image file or a base64 frame.")
        encoded = body.get("image_base64") or body.get("image")
        try:
            raw = decode_base64_image(encoded)
        except ImageError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
        return _Upload(raw, _session_id(body.get("session_id")), body)

    try:
        async with request.form(max_part_size=11 * 1024 * 1024, max_files=5, max_fields=20) as form:
            upload = form.get("file")
            if upload is None or not hasattr(upload, "read"):
                raise HTTPException(status_code=400, detail="Attach an image file or a base64 frame.")
            raw = await upload.read()
            fields = {key: form.get(key) for key in ("session_id", "confidence", "length_mm", "width_mm")}
    except HTTPException:
        raise
    except MultiPartException as exc:
        raise HTTPException(status_code=413, detail="Image must be 10 MB or smaller.") from exc
    except TypeError:
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            raise HTTPException(status_code=400, detail="Attach an image file or a base64 frame.")
        raw = await upload.read()
        fields = {key: form.get(key) for key in ("session_id", "confidence", "length_mm", "width_mm")}
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Could not read the uploaded image.") from exc
    return _Upload(raw, _session_id(fields.get("session_id")), fields)


PREVIEW_MAX_BYTES = 2 * 1024 * 1024


def _normalized_box(bbox, width: int, height: int) -> dict | None:
    if bbox is None or width <= 0 or height <= 0:
        return None
    x, y, box_w, box_h = bbox
    return {
        "x": round(max(0.0, min(1.0, x / width)), 6),
        "y": round(max(0.0, min(1.0, y / height)), 6),
        "w": round(max(0.0, min(1.0, box_w / width)), 6),
        "h": round(max(0.0, min(1.0, box_h / height)), 6),
    }


def _model_class_count() -> int:
    names = getattr(detector.model, "names", None) if detector.model is not None else None
    if isinstance(names, dict):
        return len(names)
    if isinstance(names, (list, tuple)):
        return len(names)
    return 0


def _measure(outcome, settings, categories, length_override=None, width_override=None):
    _x, _y, box_w, box_h = outcome.bbox
    length_mm, width_mm = box_to_mm(box_w, box_h, settings["pixels_per_mm"])
    if length_override is not None:
        length_mm = length_override
    if width_override is not None:
        width_mm = width_override
    length_mm = round(float(length_mm), 2)
    width_mm = round(float(width_mm), 2)
    area = round(area_mm2(length_mm, width_mm), 2)
    if settings["classification_mode"] == "model" and not detector.mock:
        predicted, used = assign_class(
            area,
            categories,
            mode="model",
            mock=False,
            class_name=outcome.class_name,
            model_class_count=_model_class_count(),
            out_of_range=settings.get("out_of_range", "nearest"),
        )
        if predicted is not None and used == "model":
            return length_mm, width_mm, area, predicted, used
    measured = class_for_measured_size(length_mm, width_mm)
    if measured:
        return length_mm, width_mm, area, measured, "length"
    predicted, used = assign_class(
        area,
        categories,
        mode=settings["classification_mode"],
        mock=detector.mock,
        class_name=outcome.class_name,
        model_class_count=_model_class_count(),
        out_of_range=settings.get("out_of_range", "nearest"),
    )
    return length_mm, width_mm, area, predicted, used


def _fit_detection_geometry(image, bbox, pixels_per_mm):
    height, width = image.shape[:2]
    x, y, box_width, box_height = bbox
    margin_x = max(4, int(round(box_width * 0.15)))
    margin_y = max(4, int(round(box_height * 0.15)))
    left = max(0, x - margin_x)
    top = max(0, y - margin_y)
    right = min(width, x + box_width + margin_x)
    bottom = min(height, y + box_height + margin_y)
    crop = image[top:bottom, left:right]
    try:
        candidates = measure_eggs(crop, pixels_per_mm=pixels_per_mm)["eggs"]
    except (EggMeasureError, cv2.error):
        return None

    target = (x - left, y - top, box_width, box_height)
    matched = []
    for candidate in candidates:
        cx, cy, candidate_width, candidate_height = (
            candidate["bbox"][key] for key in ("x", "y", "width", "height")
        )
        intersection_width = max(0, min(target[0] + target[2], cx + candidate_width) - max(target[0], cx))
        intersection_height = max(0, min(target[1] + target[3], cy + candidate_height) - max(target[1], cy))
        overlap_fraction = (intersection_width * intersection_height) / max(candidate_width * candidate_height, 1)
        center_x = candidate["ellipse"]["center_x"]
        center_y = candidate["ellipse"]["center_y"]
        if (
            target[0] <= center_x <= target[0] + target[2]
            and target[1] <= center_y <= target[1] + target[3]
            and overlap_fraction >= 0.5
        ):
            matched.append(candidate)
    if not matched:
        return None

    egg = max(matched, key=lambda item: item["score"])
    ellipse = egg["ellipse"]
    length_px = float(egg["length_px"])
    width_px = float(egg["width_px"])
    return {
        "source": "contour",
        "ellipse": {
            "center_x": round((ellipse["center_x"] + left) / width, 6),
            "center_y": round((ellipse["center_y"] + top) / height, 6),
            "major_ratio": round(ellipse["major_px"] / width, 6),
            "minor_ratio": round(ellipse["minor_px"] / width, 6),
            "angle_deg": ellipse["angle_deg"],
        },
        "major_axis_px": round(length_px, 2),
        "minor_axis_px": round(width_px, 2),
        "ellipse_major_px": round(float(ellipse["major_px"]), 2),
        "ellipse_minor_px": round(float(ellipse["minor_px"]), 2),
        "aspect_ratio": round(length_px / width_px, 3) if width_px > 0 else None,
        "area_px": egg["area_px"],
        "perimeter_px": egg["perimeter_px"],
        "length_mm": egg["length_mm"],
        "width_mm": egg["width_mm"],
        "area_mm2": (
            round(float(egg["length_mm"]) * float(egg["width_mm"]), 2)
            if egg["length_mm"] is not None and egg["width_mm"] is not None
            else None
        ),
    }


def _class_summary(items: list[dict]) -> list[dict]:
    rows = []
    for name in SIZE_NAMES:
        group = [item for item in items if item.get("class") == name]
        average = None
        if group:
            average = round(sum(float(item["confidence"]) for item in group) / len(group), 4)
        rows.append({"class": name, "count": len(group), "avg_confidence": average})
    return rows


def _preview_frame(hits, best, elapsed: float, image, settings, categories) -> dict:
    mode = "mock" if detector.mock else "real"
    height, width = image.shape[:2]
    raw_scale = float(settings["pixels_per_mm"])
    scale = raw_scale if raw_scale == raw_scale and raw_scale > 0 else None
    inference_ms = round(float(elapsed or 0), 2)
    confidence = round(float(best.confidence if best is not None else 0), 4)
    empty = {
        "detected": False,
        "bbox": _normalized_box(best.bbox, width, height) if best is not None else None,
        "class": None,
        "length_mm": None,
        "width_mm": None,
        "area_mm2": None,
        "confidence": confidence,
        "inference_ms": inference_ms,
        "mode": mode,
        "reason": "no egg",
        "egg_count": 0,
        "detections": [],
        "summary": _class_summary([]),
    }
    if not hits:
        empty["reason"] = "low confidence" if confidence > 0 else "no egg"
        if confidence <= 0:
            empty["bbox"] = None
        return empty

    detections = []
    blocked = None
    for hit in hits:
        geometry = _fit_detection_geometry(image, hit.bbox, scale)
        box_width, box_height = hit.bbox[2:]
        length_px = geometry["major_axis_px"] if geometry else float(max(box_width, box_height))
        width_px = geometry["minor_axis_px"] if geometry else float(min(box_width, box_height))
        if scale is not None:
            length_mm, width_mm, area, predicted, used = _measure(hit, settings, categories)
        else:
            length_mm = width_mm = area = None
            predicted = None
            used = "uncalibrated"
            if settings["classification_mode"] == "model" and not detector.mock:
                predicted, used = assign_class(
                    0,
                    categories,
                    mode="model",
                    mock=False,
                    class_name=hit.class_name,
                    model_class_count=_model_class_count(),
                )
        if scale is not None and predicted is None and used != "model":
            blocked = (OUT_OF_RANGE if used == "range" else CLASS_MESSAGE, length_mm, width_mm, area, hit)
            continue
        classification_reason = "unmapped model class" if predicted is None and used == "model" else None
        class_id = list(SIZE_NAMES).index(predicted) if predicted in SIZE_NAMES else None
        detections.append(
            {
                "class_id": class_id,
                "model_class_id": hit.class_id,
                "class": predicted,
                "class_name": predicted,
                "model_class": hit.class_name,
                "classification_reason": classification_reason,
                "confidence": round(float(hit.confidence), 4),
                "bbox": _normalized_box(hit.bbox, width, height),
                "length_mm": length_mm,
                "width_mm": width_mm,
                "area_mm2": area,
                "length_px": round(length_px, 2),
                "width_px": round(width_px, 2),
                "aspect_ratio": geometry["aspect_ratio"] if geometry else None,
                "area_px": geometry["area_px"] if geometry else None,
                "geometry": geometry,
                "measurement_source": "contour" if geometry else "bounding box",
                "length_source": "measured" if used == "length" else used,
            }
        )
    if not detections:
        reason, length_mm, width_mm, area, hit = blocked
        empty["reason"] = reason
        empty["bbox"] = _normalized_box(hit.bbox, width, height)
        empty["length_mm"] = length_mm
        empty["width_mm"] = width_mm
        empty["area_mm2"] = area
        empty["confidence"] = round(float(hit.confidence), 4)
        return empty

    detections.sort(key=lambda item: (item["bbox"]["x"], item["bbox"]["y"]))
    for index, item in enumerate(detections, start=1):
        item["id"] = index
    primary = max(detections, key=lambda item: item["confidence"])
    return {
        "detected": True,
        "bbox": primary["bbox"],
        "class": primary["class"],
        "class_id": primary["class_id"],
        "model_class_id": primary["model_class_id"],
        "class_name": primary["class_name"],
        "model_class": primary["model_class"],
        "classification_reason": primary["classification_reason"],
        "egg_count": len(detections),
        "length_mm": primary["length_mm"],
        "width_mm": primary["width_mm"],
        "area_mm2": primary["area_mm2"],
        "length_px": primary["length_px"],
        "width_px": primary["width_px"],
        "aspect_ratio": primary["aspect_ratio"],
        "area_px": primary["area_px"],
        "geometry": primary["geometry"],
        "measurement_source": primary["measurement_source"],
        "unit": "mm" if scale is not None else "px",
        "reason": "calibration missing" if scale is None else None,
        "confidence": primary["confidence"],
        "inference_ms": inference_ms,
        "mode": mode,
        "length_source": primary.get("length_source"),
        "detections": detections,
        "summary": _class_summary(detections),
    }


async def _read_preview(request: Request) -> bytes:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Could not read the JSON body.") from exc
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="Attach an image file or a base64 frame.")
        try:
            raw = decode_base64_image(body.get("image_base64") or body.get("image"))
        except ImageError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    else:
        try:
            async with request.form(max_part_size=PREVIEW_MAX_BYTES + 64 * 1024, max_files=2, max_fields=5) as form:
                upload = form.get("file")
                if upload is None or not hasattr(upload, "read"):
                    raise HTTPException(status_code=400, detail="Attach an image file or a base64 frame.")
                raw = await upload.read()
        except HTTPException:
            raise
        except MultiPartException as exc:
            raise HTTPException(status_code=413, detail="Preview frame must be 2 MB or smaller.") from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Could not read the uploaded image.") from exc
    if len(raw) > PREVIEW_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Preview frame must be 2 MB or smaller.")
    return raw


@router.post("/api/scan/preview")
async def preview_scan(request: Request, db: Session = Depends(get_db)):
    raw = await _read_preview(request)
    try:
        image = decode_image(raw)
    except ImageError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    settings = get_settings(db)
    categories = get_categories(db)
    frame = await run_in_threadpool(detect_frame, image, float(settings["confidence"]), wait=False)
    if frame is None:
        raise HTTPException(status_code=429, detail="Detector is busy.")
    hits, best, elapsed, error = frame
    if error:
        raise HTTPException(status_code=500, detail="The model could not read this frame. Try another image.")
    return _preview_frame(hits, best, elapsed, image, settings, categories)


@router.post("/api/scan/annotate")
async def annotate_egg(request: Request, db: Session = Depends(get_db)):
    """Annotate uploaded egg image with contour and ellipse, return measurements.

    This endpoint processes phone-captured egg images against a dark background,
    detects the egg contour, fits an ellipse, and calculates geometric measurements.
    The same processing pipeline can be reused for ESP32-CAM integration.
    """
    raw = await _read_preview(request)
    try:
        image = decode_image(raw)
    except ImageError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    settings = get_settings(db)
    pixels_per_mm = float(settings["pixels_per_mm"]) if settings.get("pixels_per_mm") else None

    try:
        result = await run_in_threadpool(annotate_uploaded_image, image, pixels_per_mm)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Annotation failed: {str(exc)}") from exc

    if not result.ok:
        detail = result.error or "Could not detect egg contour."
        raise HTTPException(status_code=422, detail=detail)

    return format_annotation_response(result)


@router.post("/api/scan")
async def create_scan(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    upload = await _read_upload(request)
    raw = upload.raw
    session_id = upload.session_id
    try:
        image = decode_image(raw)
    except ImageError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    settings = get_settings(db)
    outcome = await run_in_threadpool(detect_scaled, image, settings["confidence"], wait=True)
    if outcome is None:
        raise HTTPException(status_code=429, detail="Detector is busy.")
    if outcome.error:
        add_log(db, "ERROR", f"Scan failed: {outcome.error}")
        db.commit()
        raise HTTPException(status_code=500, detail="The model could not read this frame. Try another image.")
    if not outcome.found or outcome.bbox is None:
        detail = outcome.message or "No egg detected or confidence is below the threshold."
        confidence = round(outcome.confidence, 4)
        add_log(db, "WARN", f"{detail} Confidence {confidence:.2f}, threshold {settings['confidence']:.2f}.")
        db.commit()
        return {
            "detected": False,
            "saved": False,
            "mock": detector.mock,
            "message": detail,
            "confidence": confidence,
            "inference_ms": round(outcome.inference_ms, 2),
            "steps": _steps("captured", failed="detect", reason=detail, inference_ms=outcome.inference_ms),
        }

    x, y, box_w, box_h = outcome.bbox
    categories = get_categories(db)
    length_mm, width_mm, area, predicted, used = _measure(
        outcome, settings, categories, upload.length_mm, upload.width_mm
    )
    if predicted is None:
        detail = OUT_OF_RANGE if used == "range" else CLASS_MESSAGE
        add_log(db, "WARN", detail)
        db.commit()
        return {
            "detected": False,
            "saved": False,
            "mock": detector.mock,
            "message": detail,
            "length_mm": length_mm,
            "width_mm": width_mm,
            "area_mm2": area,
            "confidence": round(outcome.confidence, 4),
            "inference_ms": round(outcome.inference_ms, 2),
            "steps": _steps("estimate", failed="class", reason=detail, inference_ms=outcome.inference_ms),
        }
    stored_confidence = upload.confidence if upload.confidence is not None else outcome.confidence
    label = f"{predicted} {length_mm:.1f}x{width_mm:.1f}mm {stored_confidence:.0%}"
    annotated = annotate(image, outcome.bbox, label)
    jpeg = encode_jpeg(annotated)
    stem = uuid.uuid4().hex
    filename = f"{stem}.jpg"
    (scans_dir() / f"{stem}-frame.jpg").write_bytes(encode_jpeg(image))
    (scans_dir() / filename).write_bytes(jpeg)

    scan = Scan(
        image_path=filename,
        length_mm=length_mm,
        width_mm=width_mm,
        area_mm2=area,
        predicted_class=predicted,
        actual_class=None,
        confidence=round(stored_confidence, 4),
        inference_ms=round(outcome.inference_ms, 2),
        is_mock=detector.mock,
        operator_email=user.email,
        model_version=active_model()["version"],
        session_id=session_id,
    )
    db.add(scan)
    db.flush()
    add_log(
        db,
        "INFO",
        f"{user.email} saved Live Scan record #{scan.id} as {predicted} "
        f"({length_mm:.1f} x {width_mm:.1f} mm)"
        + (" [mock]" if detector.mock else "")
        + ".",
    )
    db.commit()
    db.refresh(scan)
    payload = scan_dict(scan)
    class_id = list(SIZE_NAMES).index(predicted) if predicted in SIZE_NAMES else None
    message = f"Egg classified as {predicted}."
    if outcome.egg_count > 1:
        message += " Multiple eggs detected. Showing the highest-confidence egg."
    payload.update(
        {
            "detected": True,
            "saved": True,
            "mock": detector.mock,
            "message": message,
            "class_id": class_id,
            "class_name": predicted,
            "egg_count": outcome.egg_count,
            "bbox": {"x": x, "y": y, "width": box_w, "height": box_h},
            "annotated_image": to_data_url(jpeg),
            "steps": _steps("shown", inference_ms=outcome.inference_ms),
        }
    )
    return payload


def _require_study_class(value: str, label: str) -> None:
    if value and value not in SIZE_NAMES:
            raise HTTPException(
                status_code=422,
                detail=f"{label} must be one of Small, Medium, Large, Extra Large or Jumbo.",
            )


def _filtered_scans(
    db: Session,
    q: str,
    class_name: str,
    correctness: str,
    date_from: str = "",
    date_to: str = "",
    actual_name: str = "",
):
    query = db.query(Scan)
    _require_study_class(class_name, "predicted_class")
    _require_study_class(actual_name, "actual_class")
    if q:
        like = f"%{q.strip().lower()}%"
        query = query.filter(
            or_(
                func.lower(Scan.predicted_class).like(like),
                func.lower(func.coalesce(Scan.actual_class, "")).like(like),
                cast(Scan.id, String).like(like),
            )
        )
    if class_name:
        query = query.filter(Scan.predicted_class == class_name)
    if actual_name:
        query = query.filter(Scan.actual_class == actual_name)
    if date_from:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_from[:10]) or len(date_from) < 10:
            raise HTTPException(status_code=400, detail="date_from must be YYYY-MM-DD.")
        query = query.filter(func.date(Scan.created_at) >= date_from[:10])
    if date_to:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_to[:10]) or len(date_to) < 10:
            raise HTTPException(status_code=400, detail="date_to must be YYYY-MM-DD.")
        query = query.filter(func.date(Scan.created_at) <= date_to[:10])
    if correctness == "correct":
        query = query.filter(Scan.actual_class.isnot(None), Scan.actual_class == Scan.predicted_class)
    elif correctness == "miss":
        query = query.filter(Scan.actual_class.isnot(None), Scan.actual_class != Scan.predicted_class)
    elif correctness == "unlabeled":
        query = query.filter(Scan.actual_class.is_(None))
    elif correctness not in ("", "all"):
        raise HTTPException(status_code=400, detail="Filter must be all, correct, miss, or unlabeled.")
    return query


@router.get("/api/scans")
def list_scans(
    q: str = "",
    class_name: str = "",
    correctness: str = "all",
    date_from: str = "",
    date_to: str = "",
    actual_name: str = "",
    include_summary: bool = False,
    page: int = 1,
    page_size: int = 10,
    db: Session = Depends(get_db),
):
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    query = _filtered_scans(db, q, class_name, correctness, date_from, date_to, actual_name)
    total = query.count()
    pages = max((total + page_size - 1) // page_size, 1)
    page = min(page, pages)
    rows = (
        query.order_by(Scan.created_at.desc(), Scan.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    response = {
        "items": [scan_dict(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }
    if include_summary:
        verified = Scan.actual_class.isnot(None)
        correct = verified & (Scan.actual_class == Scan.predicted_class)
        incorrect = verified & (Scan.actual_class != Scan.predicted_class)
        counts = db.query(
            func.count(Scan.id),
            func.coalesce(func.sum(case((verified, 1), else_=0)), 0),
            func.coalesce(func.sum(case((correct, 1), else_=0)), 0),
            func.coalesce(func.sum(case((incorrect, 1), else_=0)), 0),
        ).one()
        response["summary"] = {
            "total": int(counts[0] or 0),
            "verified": int(counts[1] or 0),
            "correct": int(counts[2] or 0),
            "incorrect": int(counts[3] or 0),
            "pending": int((counts[0] or 0) - (counts[1] or 0)),
        }
    return response


@router.get("/api/scans/sessions")
def list_sessions(db: Session = Depends(get_db)):
    rows = (
        db.query(Scan.session_id, func.count(Scan.id))
        .filter(Scan.session_id.isnot(None), Scan.session_id != "")
        .group_by(Scan.session_id)
        .order_by(func.max(Scan.created_at).desc())
        .all()
    )
    return {"sessions": [{"id": session_id, "scans": count} for session_id, count in rows]}


@router.get("/api/scans/export")
def export_scans(
    q: str = "",
    class_name: str = "",
    correctness: str = "all",
    date_from: str = "",
    date_to: str = "",
    actual_name: str = "",
    report: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = (
        _filtered_scans(db, q, class_name, correctness, date_from, date_to, actual_name)
        .order_by(Scan.created_at.asc(), Scan.id.asc())
        .all()
    )
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    if report:
        writer.writerow(
            [
                "Record ID",
                "Scan Date",
                "Scan Time (UTC)",
                "Predicted Egg Size Class",
                "Actual Egg Size Class",
                "Egg Length (mm)",
                "Egg Width (mm)",
                "Measured Area (mm²)",
                "Detection Confidence",
                "Classification Result",
                "Operator",
                "Model Identifier",
                "Scan Session ID",
            ]
        )
    else:
        writer.writerow(
            [
                "id",
                "created_at",
                "predicted_class",
                "actual_class",
                "length_mm",
                "width_mm",
                "area_mm2",
                "confidence",
                "inference_ms",
                "is_mock",
                "correct",
                "operator_email",
                "model_version",
                "session_id",
            ]
        )
    for row in rows:
        item = scan_dict(row)
        if report:
            timestamp = item["created_at"] or ""
            scan_date, separator, scan_time = timestamp.partition("T")
            if scan_time.endswith("Z"):
                scan_time = scan_time[:-1]
            if item["correct"] is None:
                classification_result = "Awaiting verification"
            elif item["correct"]:
                classification_result = "Correct"
            else:
                classification_result = "Mismatch"
            writer.writerow(
                [
                    item["id"],
                    scan_date if separator else "",
                    scan_time if separator else "",
                    item["predicted_class"],
                    item["actual_class"] or "",
                    item["length_mm"],
                    item["width_mm"],
                    item["area_mm2"],
                    item["confidence"],
                    classification_result,
                    item["operator_email"] or "",
                    item["model_version"] or "",
                    item["session_id"] or "",
                ]
            )
        else:
            writer.writerow(
                [
                    item["id"],
                    item["created_at"],
                    item["predicted_class"],
                    item["actual_class"] or "",
                    item["length_mm"],
                    item["width_mm"],
                    item["area_mm2"],
                    item["confidence"],
                    item["inference_ms"],
                    item["is_mock"],
                    "" if item["correct"] is None else item["correct"],
                    item["operator_email"] or "",
                    item["model_version"] or "",
                    item["session_id"] or "",
                ]
            )
    export_target = "Report" if report else "Scan Records"
    add_log(db, "INFO", f"{user.email} exported {len(rows)} {export_target} record(s) to CSV.")
    db.commit()
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=EggScan_Report_"
            f"{datetime.now(timezone.utc).date().isoformat()}.csv"
            if report
            else "attachment; filename=eggscan-records.csv",
        },
    )


@router.get("/api/scans/{scan_id}/image")
def scan_image(scan_id: int, db: Session = Depends(get_db)):
    scan = db.get(Scan, scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan not found.")
    path = scans_dir() / Path(scan.image_path).name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Image file is missing.")
    return FileResponse(path, media_type="image/jpeg")


@router.patch("/api/scans/{scan_id}")
def update_scan(
    scan_id: int,
    body: ScanPatch,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    scan = db.get(Scan, scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan not found.")
    chosen = body.actual_class
    if chosen is not None and chosen not in SIZE_NAMES:
        raise HTTPException(status_code=422, detail=CLASS_MESSAGE)
    scan.actual_class = chosen
    if chosen is None:
        add_log(db, "INFO", f"{user.email} cleared the actual size class on Scan Records scan #{scan.id}.")
    else:
        add_log(db, "INFO", f"{user.email} verified Scan Records scan #{scan.id} as {chosen}.")
    db.commit()
    db.refresh(scan)
    return scan_dict(scan)


@router.post("/api/scans/{scan_id}/reclassify")
def reclassify_scan(
    scan_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    scan = db.get(Scan, scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan not found.")
    path = scans_dir() / Path(scan.image_path).name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Image file is missing.")
    try:
        image = decode_image(path.read_bytes())
    except ImageError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    settings = get_settings(db)
    outcome = detector.detect(image, settings["confidence"])
    if outcome.error:
        add_log(db, "ERROR", f"Re-classify failed for scan #{scan.id}: {outcome.error}")
        db.commit()
        raise HTTPException(status_code=500, detail="The model could not read this frame. Try another image.")
    if not outcome.found or outcome.bbox is None:
        detail = outcome.message or "No egg detected or confidence is below the threshold."
        add_log(db, "WARN", f"Re-classify of scan #{scan.id} was not saved. {detail}")
        db.commit()
        raise HTTPException(status_code=422, detail=detail)

    x, y, box_w, box_h = outcome.bbox
    categories = get_categories(db)
    length_mm, width_mm, area, predicted, used = _measure(outcome, settings, categories)
    if predicted is None:
        detail = OUT_OF_RANGE if used == "range" else CLASS_MESSAGE
        raise HTTPException(status_code=422, detail=detail)
    label = f"{predicted} {length_mm:.1f}x{width_mm:.1f}mm {outcome.confidence:.0%}"
    jpeg = encode_jpeg(annotate(image, outcome.bbox, label))
    path.write_bytes(jpeg)
    scan.length_mm = length_mm
    scan.width_mm = width_mm
    scan.area_mm2 = area
    scan.predicted_class = predicted
    scan.confidence = round(outcome.confidence, 4)
    scan.inference_ms = round(outcome.inference_ms, 2)
    scan.is_mock = detector.mock
    scan.model_version = active_model()["version"]
    add_log(
        db,
        "INFO",
        f"{user.email} re-classified Scan Records scan #{scan.id} as {predicted} "
        f"({length_mm:.1f} x {width_mm:.1f} mm).",
    )
    db.commit()
    db.refresh(scan)
    payload = scan_dict(scan)
    payload["annotated_image"] = to_data_url(jpeg)
    payload["bbox"] = {"x": x, "y": y, "width": box_w, "height": box_h}
    return payload


@router.delete("/api/scans/{scan_id}")
def delete_scan(
    scan_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    scan = db.get(Scan, scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan not found.")
    path = scans_dir() / Path(scan.image_path).name
    db.delete(scan)
    add_log(db, "INFO", f"{user.email} deleted Scan Records scan #{scan_id}.")
    db.commit()
    if path.is_file():
        path.unlink()
    frame = path.with_name(f"{path.stem}-frame.jpg")
    if frame.is_file():
        frame.unlink()
    return {"ok": True}
