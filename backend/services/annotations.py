"""Oval annotations for uploaded class-folder images.

One image can hold several eggs. Length is the longer diameter of each oval.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path

from backend import db as database
from backend.models import Annotation
from backend.services.annotation_labels import FILENAME_LABELS, filename_reference_label
from backend.services.dataset_prep import CLASSES, _incoming_dir, normalize_key
from backend.services.images import ImageError, decode_image

_BY_KEY = {item["key"]: item for item in CLASSES}
_BY_SLUG = {item["slug"]: item for item in CLASSES}


class AnnotationRejected(ValueError):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def spec_for(value: str) -> dict:
    key = normalize_key(value)
    if key and key in _BY_KEY:
        return _BY_KEY[key]
    slug = str(value or "").strip().lower().replace("-", "_")
    if slug in _BY_SLUG:
        return _BY_SLUG[slug]
    raise AnnotationRejected("Use small, medium, large, extra large, or jumbo.")


def _safe_name(filename: str) -> str:
    name = Path(str(filename or "").replace("\\", "/")).name
    if not name or name != filename or name.startswith(".") or ".." in name:
        raise AnnotationRejected("That image was not found in this class.")
    return name


def image_path(spec: dict, filename: str) -> Path:
    path = _incoming_dir(spec["slug"]) / _safe_name(filename)
    if not path.is_file():
        raise AnnotationRejected("That image was not found in this class.")
    return path


def _image_size(path: Path) -> tuple[int, int]:
    try:
        image = decode_image(path.read_bytes())
    except ImageError as exc:
        raise AnnotationRejected("That image could not be read.") from exc
    height, width = image.shape[:2]
    return width, height


def _record(row: Annotation) -> dict:
    return {
        "id": row.id,
        "image": row.image_key,
        "class": row.class_name,
        "reference_label": {
            "class_name": row.reference_class_name,
            "source": row.reference_label_source,
        },
        "reviewed": row.reviewed,
        "length_mm": row.length_mm,
        "width_mm": row.width_mm,
        "measurement": {
            "start_x": row.start_x,
            "start_y": row.start_y,
            "end_x": row.end_x,
            "end_y": row.end_y,
            "pixel_length": row.pixel_length,
            "pixel_width": row.pixel_width,
        },
        "calibration": row.calibration,
        "pixels_per_mm": row.pixels_per_mm,
        "geometry": row.geometry,
        "annotated": True,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def list_images(spec: dict) -> dict:
    folder = _incoming_dir(spec["slug"])
    names = []
    if folder.is_dir():
        names = sorted(
            path.name
            for path in folder.iterdir()
            if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
        )
    grouped: dict[str, list[Annotation]] = {}
    for row in _rows_for_slug(spec["slug"]):
        if "/" not in row.image_key:
            continue
        grouped.setdefault(row.image_key.split("/", 1)[1], []).append(row)
    images = []
    for name in names:
        rows = grouped.get(name) or []
        last = rows[-1] if rows else None
        images.append(
            {
                "name": name,
                "annotated": bool(rows),
                "ovals": len(rows),
                "length_mm": None if last is None else last.length_mm,
                "width_mm": None if last is None else last.width_mm,
            }
        )
    annotated = sum(1 for item in images if item["annotated"])
    return {
        "class": spec["name"],
        "class_key": spec["key"],
        "slug": spec["slug"],
        "images": images,
        "annotated": annotated,
        "total": len(images),
    }


def annotation_counts() -> dict[str, int]:
    counts = {spec["key"]: 0 for spec in CLASSES}
    if database.SessionLocal is None:
        return counts
    db = database.SessionLocal()
    try:
        rows = db.query(Annotation).all()
    finally:
        db.close()
    seen: set[str] = set()
    for row in rows:
        slug, _, name = row.image_key.partition("/")
        spec = _BY_SLUG.get(slug)
        if spec is None or not name or row.image_key in seen:
            continue
        if (_incoming_dir(slug) / name).is_file():
            seen.add(row.image_key)
            counts[spec["key"]] += 1
    return counts


def get_annotation(spec: dict, filename: str) -> dict | None:
    image_path(spec, filename)
    key = f"{spec['slug']}/{_safe_name(filename)}"
    rows = _rows_for_key(key)
    return None if not rows else _bundle(rows)


def save_annotation(spec: dict, filename: str, body: dict, pixels_per_mm: float | None = None) -> dict:
    path = image_path(spec, filename)
    width, height = _image_size(path)
    ovals = body.get("ovals")
    if not isinstance(ovals, list) or not ovals:
        raise AnnotationRejected("Draw an oval around at least one egg.")
    if len(ovals) > 40:
        raise AnnotationRejected("An image can have at most 40 ovals.")
    prepared = [_prepare_oval(oval, width, height, pixels_per_mm) for oval in ovals]
    reference_class = body.get("reference_class")
    label_source = body.get("reference_label_source")
    if reference_class is not None:
        reference_class = str(reference_class).strip()
        if reference_class not in FILENAME_LABELS.values():
            raise AnnotationRejected("Choose Small, Medium, Large, Extra Large, or Jumbo as the reference label.")
        if label_source not in ("filename", "manual"):
            raise AnnotationRejected("Choose whether the reference label came from the filename or manual review.")
        if label_source == "filename" and filename_reference_label(filename) != reference_class:
            raise AnnotationRejected("The filename does not contain that one unambiguous reference class.")
    elif label_source is not None:
        raise AnnotationRejected("A reference-label source requires a reference class.")
    key = f"{spec['slug']}/{path.name}"
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db = database.SessionLocal()
    try:
        existing = db.query(Annotation).filter(Annotation.image_key == key).order_by(Annotation.id).all()
        if any(row.class_name != spec["name"] for row in existing):
            raise AnnotationRejected("This image belongs to a different size class.")
        for row in existing:
            db.delete(row)
        db.flush()
        saved = []
        for oval in prepared:
            row = Annotation(
                image_key=key,
                class_name=spec["name"],
                reference_class_name=reference_class,
                reference_label_source=label_source,
                reviewed=bool(body.get("reviewed", False)),
                length_mm=oval["length_mm"],
                width_mm=oval["width_mm"],
                start_x=oval["start_x"],
                start_y=oval["start_y"],
                end_x=oval["end_x"],
                end_y=oval["end_y"],
                pixel_length=oval["pixel_length"],
                pixel_width=oval["pixel_width"],
                geometry=oval["geometry"],
                calibration=oval["calibration"],
                pixels_per_mm=oval["pixels_per_mm"],
                created_at=now,
                updated_at=now,
            )
            db.add(row)
            saved.append(row)
        db.commit()
        for row in saved:
            db.refresh(row)
        return _bundle(saved)
    finally:
        db.close()


def delete_annotation(spec: dict, filename: str) -> None:
    image_path(spec, filename)
    key = f"{spec['slug']}/{_safe_name(filename)}"
    db = database.SessionLocal()
    try:
        rows = db.query(Annotation).filter(Annotation.image_key == key).all()
        if not rows:
            raise AnnotationRejected("This image has no saved oval.")
        for row in rows:
            db.delete(row)
        db.commit()
    finally:
        db.close()


def drop_missing(spec: dict) -> None:
    """Remove annotations whose image was replaced by a new upload."""
    if database.SessionLocal is None:
        return
    prefix = spec["slug"] + "/"
    db = database.SessionLocal()
    try:
        rows = db.query(Annotation).filter(Annotation.image_key.like(prefix + "%")).all()
        folder = _incoming_dir(spec["slug"])
        for row in rows:
            name = row.image_key.split("/", 1)[1]
            if not (folder / name).is_file() or row.class_name != spec["name"]:
                db.delete(row)
        db.commit()
    finally:
        db.close()


def boxes_for_incoming() -> dict[str, dict]:
    """Reviewed egg contours and their pixel-space bounds for uploaded images."""
    found: dict[str, dict] = {}
    if database.SessionLocal is None:
        return found
    db = database.SessionLocal()
    try:
        rows = db.query(Annotation).order_by(Annotation.id).all()
    finally:
        db.close()
    for row in rows:
        slug, _, name = row.image_key.partition("/")
        if not slug or not name or not (_incoming_dir(slug) / name).is_file():
            continue
        geometry = row.geometry if isinstance(row.geometry, dict) else {}
        bbox = geometry.get("bbox") if isinstance(geometry.get("bbox"), dict) else {}
        left = float(bbox.get("x", min(row.start_x, row.end_x)))
        top = float(bbox.get("y", min(row.start_y, row.end_y)))
        box_w = float(bbox.get("width", abs(row.end_x - row.start_x)))
        box_h = float(bbox.get("height", abs(row.end_y - row.start_y)))
        if box_w < 2 or box_h < 2:
            continue
        item = found.setdefault(row.image_key, {"ovals": [], "reviewed": True})
        item["reviewed"] = item["reviewed"] and bool(row.reviewed)
        item["ovals"].append(
            {
                "length_mm": None if row.length_mm is None else float(row.length_mm),
                "width_mm": None if row.width_mm is None else float(row.width_mm),
                "length_px": float(row.pixel_length),
                "width_px": None if row.pixel_width is None else float(row.pixel_width),
                "area_px": None if not geometry else geometry.get("area_px"),
                "box": (left, top, box_w, box_h),
            }
        )
    return found


def _rows_for_slug(slug: str) -> list[Annotation]:
    if database.SessionLocal is None:
        return []
    db = database.SessionLocal()
    try:
        return (
            db.query(Annotation)
            .filter(Annotation.image_key.like(slug + "/%"))
            .order_by(Annotation.id)
            .all()
        )
    finally:
        db.close()


def _rows_for_key(key: str) -> list[Annotation]:
    if database.SessionLocal is None:
        return []
    db = database.SessionLocal()
    try:
        return db.query(Annotation).filter(Annotation.image_key == key).order_by(Annotation.id).all()
    finally:
        db.close()


def _bundle(rows: list[Annotation]) -> dict:
    ovals = [_record(row) for row in rows]
    last = ovals[-1]
    return {
        "image": last["image"],
        "class": last["class"],
        "reference_label": last["reference_label"],
        "reviewed": all(item["reviewed"] for item in ovals),
        "count": len(ovals),
        "ovals": ovals,
        "length_mm": last["length_mm"],
        "width_mm": last["width_mm"],
        "measurement": last["measurement"],
        "calibration": last["calibration"],
        "pixels_per_mm": last["pixels_per_mm"],
        "annotated": True,
        "created_at": ovals[0]["created_at"],
        "updated_at": last["updated_at"],
    }


def _prepare_oval(body: dict, width: int, height: int, pixels_per_mm: float | None) -> dict:
    start_x, start_y, end_x, end_y = _points(body, width, height)
    left, right = sorted((start_x, end_x))
    top, bottom = sorted((start_y, end_y))
    pixel_wide = right - left
    pixel_tall = bottom - top
    if pixel_wide < 2 or pixel_tall < 2:
        raise AnnotationRejected("Draw an oval around the egg. Both diameters need to be visible.")
    calibration = str(body.get("calibration") or "manual").strip().lower()
    if calibration not in ("manual", "scale", "pixels"):
        calibration = "pixels"
        body = {**body, "length_mm": None, "width_mm": None}
    length_value = body.get("length_mm")
    width_value = body.get("width_mm")
    if calibration == "scale":
        supplied_scale = body.get("pixels_per_mm")
        active_scale = supplied_scale if supplied_scale is not None else pixels_per_mm
        if active_scale is None or not math.isfinite(float(active_scale)) or float(active_scale) <= 0:
            calibration = "pixels"
            length_value = width_value = None
    if (length_value is None) != (width_value is None):
        raise AnnotationRejected("Provide both physical measurements or leave both blank to save pixel dimensions only.")
    if length_value is None:
        length_mm = width_mm = None
        calibration = "pixels"
        scale = None
    else:
        if calibration == "pixels":
            raise AnnotationRejected("Pixel-only annotations cannot include millimetre measurements.")
        length_mm = _positive(length_value, "Measured length")
        width_mm = _positive(width_value, "Measured width")
        if width_mm > length_mm:
            length_mm, width_mm = width_mm, length_mm
        scale_value = body.get("pixels_per_mm")
        scale = _positive(scale_value, "Pixels per mm") if scale_value is not None else pixels_per_mm
        if scale is not None and (not math.isfinite(scale) or scale <= 0):
            scale = None
    geometry = body.get("geometry")
    if geometry is not None and not isinstance(geometry, dict):
        raise AnnotationRejected("Annotation geometry must be an object.")
    pixel_length = max(pixel_wide, pixel_tall)
    pixel_width = min(pixel_wide, pixel_tall)
    if isinstance(geometry, dict):
        points = geometry.get("contour")
        if points is not None:
            if not isinstance(points, list) or len(points) < 5 or len(points) > 4096:
                raise AnnotationRejected("The saved contour must have between 5 and 4096 points.")
            checked_points = []
            for point in points:
                if not isinstance(point, (list, tuple)) or len(point) != 2:
                    raise AnnotationRejected("Each contour point must contain x and y coordinates.")
                px = _finite(point[0], "Contour x")
                py = _finite(point[1], "Contour y")
                if px < 0 or px >= width or py < 0 or py >= height:
                    raise AnnotationRejected("A contour point is outside the image.")
                checked_points.append([round(px, 2), round(py, 2)])
            geometry = {**geometry, "contour": checked_points}
            ellipse = geometry.get("ellipse") if isinstance(geometry.get("ellipse"), dict) else geometry
            center_x = _finite(ellipse.get("center_x"), "Ellipse center x")
            center_y = _finite(ellipse.get("center_y"), "Ellipse center y")
            angle = math.radians(_finite(ellipse.get("angle_deg"), "Ellipse angle"))
            axis = (math.cos(angle), math.sin(angle))
            perpendicular = (-axis[1], axis[0])
            along = [
                (point[0] - center_x) * axis[0] + (point[1] - center_y) * axis[1]
                for point in checked_points
            ]
            across = [
                (point[0] - center_x) * perpendicular[0] + (point[1] - center_y) * perpendicular[1]
                for point in checked_points
            ]
            pixel_length = max(along) - min(along)
            pixel_width = max(across) - min(across)
            geometry = {
                **geometry,
                "length_px": round(pixel_length, 2),
                "width_px": round(pixel_width, 2),
            }
        else:
            pixel_length = _finite(geometry.get("length_px", geometry.get("major_px", pixel_length)), "Pixel length")
            pixel_width = _finite(geometry.get("width_px", geometry.get("minor_px", pixel_width)), "Pixel width")
        if pixel_length <= 0 or pixel_width <= 0:
            raise AnnotationRejected("Pixel measurements must be greater than 0.")
    if calibration == "scale":
        if scale is None:
            calibration = "manual"
        else:
            expected_length = pixel_length / scale
            expected_width = pixel_width / scale
            length_ok = abs(expected_length - length_mm) <= max(0.5, expected_length * 0.05)
            width_ok = abs(expected_width - width_mm) <= max(0.5, expected_width * 0.05)
            if not (length_ok and width_ok):
                calibration = "manual"
    return {
        "start_x": round(left, 2),
        "start_y": round(top, 2),
        "end_x": round(right, 2),
        "end_y": round(bottom, 2),
        "pixel_length": round(pixel_length, 2),
        "pixel_width": round(pixel_width, 2),
        "length_mm": None if length_mm is None else round(length_mm, 2),
        "width_mm": None if width_mm is None else round(width_mm, 2),
        "calibration": calibration,
        "pixels_per_mm": None if calibration != "scale" or scale is None else round(scale, 4),
        "geometry": geometry,
    }


def _points(body: dict, width: int, height: int) -> tuple[float, float, float, float]:
    values = []
    for label, limit in (("start_x", width), ("start_y", height), ("end_x", width), ("end_y", height)):
        number = _finite(body.get(label), label)
        if number < 0 or number > limit:
            raise AnnotationRejected(f"{label} is outside the image.")
        values.append(number)
    return values[0], values[1], values[2], values[3]


def _finite(value, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise AnnotationRejected(f"{label} must be a number.") from exc
    if not math.isfinite(number):
        raise AnnotationRejected(f"{label} must be a number.")
    return number


def _positive(value, label: str) -> float:
    number = _finite(value, label)
    if number <= 0:
        raise AnnotationRejected(f"{label} must be greater than 0.")
    return number
