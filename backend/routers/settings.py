"""Camera scale, classification mode, and size classes."""

from __future__ import annotations

import cv2
import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from starlette.formparsers import MultiPartException

from backend.auth import get_current_user
from backend.db import get_db
from backend.models import Scan, User
from backend.schemas import CameraCalibrateIn, CategoriesIn, FitBandsIn, SettingsIn
from backend.services.images import ImageError, decode_image
from backend.services.audit import add_log
from backend.services.detector import detector
from backend.services.serialize import category_dict
from backend.services.settings_store import (
    get_categories,
    get_settings,
    has_settings_undo,
    replace_categories,
    reset_categories,
    save_settings,
    undo_settings,
)
from backend.services.size import class_for_area, fit_size_bands

router = APIRouter(tags=["settings"], dependencies=[Depends(get_current_user)])


def _public_settings(db: Session) -> dict:
    payload = get_settings(db)
    payload["mock_mode"] = detector.mock
    payload["can_undo"] = has_settings_undo(db)
    return payload


@router.get("/api/settings")
def read_settings(db: Session = Depends(get_db)):
    return _public_settings(db)


@router.put("/api/settings")
def update_settings(
    body: SettingsIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    saved = save_settings(db, body.model_dump())
    add_log(
        db,
        "INFO",
        f"{user.email} updated detection settings "
        f"(confidence {saved['confidence']}, pixels/mm {saved['pixels_per_mm']}, "
        f"mode {saved['classification_mode']}, outside {saved['out_of_range']}, camera {saved['camera_index']}).",
    )
    db.commit()
    return _public_settings(db)


@router.post("/api/settings/undo")
def undo_saved_settings(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        undo_settings(db)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    add_log(db, "INFO", f"{user.email} reverted the last saved detection settings.")
    db.commit()
    return _public_settings(db)


@router.post("/api/settings/fit-bands")
def fit_bands(
    body: FitBandsIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rows = db.query(Scan).filter(Scan.actual_class.isnot(None), Scan.area_mm2.isnot(None)).all()
    categories = get_categories(db)
    try:
        fitted = fit_size_bands([(row.actual_class, row.area_mm2) for row in rows], categories)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    class _Band:
        def __init__(self, item, order):
            self.name = item["name"]
            self.min_area = item["min_area"]
            self.sort_order = order
            self.color = item["color"]

    proposed = [_Band(item, index) for index, item in enumerate(fitted["bands"])]
    before_hits = sum(1 for row in rows if row.predicted_class == row.actual_class)
    after_hits = sum(1 for row in rows if class_for_area(row.area_mm2, proposed) == row.actual_class)
    current = {cat.name: cat.min_area for cat in categories}
    payload = {
        "confirmed": False,
        "sample_count": fitted["sample_count"],
        "warnings": fitted["warnings"],
        "before_accuracy": round(before_hits / len(rows), 4) if rows else None,
        "after_accuracy": round(after_hits / len(rows), 4) if rows else None,
        "bands": [
            {
                "name": item["name"],
                "color": item["color"],
                "current_min_area": current.get(item["name"]),
                "min_area": item["min_area"],
                "median_area_mm2": item["median_area_mm2"],
                "count": item["count"],
            }
            for item in fitted["bands"]
        ],
    }
    if not body.confirm:
        return payload
    replace_categories(
        db,
        [{"name": band.name, "min_area": band.min_area, "color": band.color} for band in proposed],
    )
    add_log(db, "INFO", f"{user.email} fitted size bands from {fitted['sample_count']} verified eggs.")
    db.commit()
    payload["confirmed"] = True
    return payload


def _longest_side_px(image: np.ndarray) -> float | None:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    contour = max(contours, key=cv2.contourArea)
    if cv2.contourArea(contour) < 200:
        return None
    _center, size, _angle = cv2.minAreaRect(contour)
    width, height = size
    longer = float(max(width, height))
    if longer <= 0:
        return None
    return longer


@router.post("/api/settings/measure-reference")
async def measure_reference(request: Request):
    try:
        async with request.form(max_part_size=11 * 1024 * 1024, max_files=5, max_fields=10) as form:
            upload = form.get("file")
            if upload is None or not hasattr(upload, "read"):
                raise HTTPException(status_code=400, detail="Attach an image of the reference object.")
            raw = await upload.read()
    except HTTPException:
        raise
    except MultiPartException as exc:
        raise HTTPException(status_code=413, detail="Image must be 10 MB or smaller.") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Could not read the uploaded image.") from exc
    try:
        image = decode_image(raw)
    except ImageError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    pixel_length = _longest_side_px(image)
    if pixel_length is None:
        raise HTTPException(
            status_code=422,
            detail="No reference object was found. Draw its length on the frame instead.",
        )
    return {"pixel_length": round(pixel_length, 2)}


@router.post("/api/settings/calibrate-camera")
def calibrate_camera(
    body: CameraCalibrateIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    current = get_settings(db)
    previous = float(current["pixels_per_mm"])
    updated = body.pixel_length / body.known_mm
    if updated <= 0 or updated > 1000:
        raise HTTPException(status_code=400, detail="pixels per mm must be between 0 and 1000.")
    current["pixels_per_mm"] = round(updated, 4)
    save_settings(db, current)
    add_log(
        db,
        "INFO",
        f"{user.email} updated camera scale from {previous:.4f} to {current['pixels_per_mm']:.4f} pixels/mm "
        f"({body.pixel_length:.1f} px over {body.known_mm:.1f} mm).",
    )
    db.commit()
    return {
        "previous_pixels_per_mm": round(previous, 4),
        "pixels_per_mm": current["pixels_per_mm"],
        "known_mm": body.known_mm,
        "pixel_length": body.pixel_length,
    }


@router.post("/api/categories/reset")
def reset_size_classes(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = reset_categories(db)
    add_log(db, "INFO", f"{user.email} reset size classes to Small, Medium, Large, Extra Large and Jumbo.")
    db.commit()
    return {"categories": [category_dict(row) for row in rows]}


@router.get("/api/categories")
def read_categories(db: Session = Depends(get_db)):
    return {"categories": [category_dict(row) for row in get_categories(db)]}


@router.put("/api/categories")
def update_categories(
    body: CategoriesIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        rows = replace_categories(db, [item.model_dump() for item in body.categories])
    except ValueError as exc:
        detail = str(exc)
        status = 422 if "must be one of" in detail else 400
        raise HTTPException(status_code=status, detail=detail) from exc
    add_log(db, "INFO", f"{user.email} updated size categories and existing scans were reclassified.")
    db.commit()
    return {"categories": [category_dict(row) for row in rows]}
