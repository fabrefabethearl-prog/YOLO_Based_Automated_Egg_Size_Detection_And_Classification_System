"""Read and write settings and size classes."""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.db import DEFAULT_CATEGORIES, DEFAULT_SETTINGS
from backend.models import Category, Scan, Setting
from backend.services.audit import add_log
from backend.services.size import class_for_area

SIZE_NAMES = tuple(item["name"] for item in DEFAULT_CATEGORIES)
CLASS_MESSAGE = "Size class must be one of Small, Medium, Large, Extra Large or Jumbo."


def get_settings(db: Session) -> dict:
    stored = dict(DEFAULT_SETTINGS)
    for row in db.query(Setting).all():
        if row.key in ("k", "a", "b"):
            continue
        stored[row.key] = row.value
    mode = stored.get("classification_mode") or "dimensions"
    if mode not in ("model", "dimensions"):
        mode = "dimensions"
    outside = stored.get("out_of_range") or "nearest"
    if outside not in ("nearest", "reject"):
        outside = "nearest"
    return {
        "confidence": float(stored["confidence"]),
        "pixels_per_mm": float(stored["pixels_per_mm"]),
        "camera_index": int(stored["camera_index"]),
        "classification_mode": mode,
        "out_of_range": outside,
    }


def has_settings_undo(db: Session) -> bool:
    row = db.get(Setting, "settings_previous")
    return isinstance(getattr(row, "value", None), dict)


def save_settings(db: Session, values: dict, *, remember: bool = True) -> dict:
    if remember:
        previous = get_settings(db)
        snapshot = db.get(Setting, "settings_previous")
        if snapshot is None:
            db.add(Setting(key="settings_previous", value=previous))
        else:
            snapshot.value = previous
    for key, value in values.items():
        row = db.get(Setting, key)
        if row is None:
            db.add(Setting(key=key, value=value))
        else:
            row.value = value
    db.commit()
    return get_settings(db)


def undo_settings(db: Session) -> dict:
    snapshot = db.get(Setting, "settings_previous")
    if snapshot is None or not isinstance(snapshot.value, dict):
        raise ValueError("No saved settings to undo.")
    previous = dict(snapshot.value)
    db.delete(snapshot)
    db.flush()
    return save_settings(db, previous, remember=False)


def get_categories(db: Session) -> list[Category]:
    return db.query(Category).order_by(Category.sort_order.asc(), Category.min_area.asc()).all()


def validate_category_payload(items: list[dict]) -> list[dict]:
    if [str(item.get("name", "")).strip() for item in items] != list(SIZE_NAMES):
        raise ValueError(CLASS_MESSAGE + " Names stay in that order.")
    previous = None
    cleaned = []
    for item, official in zip(items, DEFAULT_CATEGORIES):
        min_area = float(item["min_area"])
        if previous is not None and min_area <= previous:
            raise ValueError("Thresholds must strictly increase: Small < Medium < Large < Extra Large < Jumbo.")
        previous = min_area
        cleaned.append({"name": official["name"], "min_area": min_area, "color": official["color"]})
    return cleaned


def replace_categories(db: Session, items: list[dict]) -> list[Category]:
    cleaned = validate_category_payload(items)
    db.query(Category).delete()
    db.flush()
    for index, item in enumerate(cleaned):
        db.add(
            Category(
                name=item["name"],
                min_area=item["min_area"],
                color=item["color"],
                sort_order=index,
            )
        )
    db.flush()
    db.expire_all()
    recompute_classes(db)
    db.commit()
    return get_categories(db)


def reset_categories(db: Session) -> list[Category]:
    return replace_categories(db, [dict(item) for item in DEFAULT_CATEGORIES])


def recompute_classes(db: Session) -> int:
    """Recompute predicted size classes from area. Actual class stays as the user set it."""
    categories = get_categories(db)
    updated = 0
    for scan in db.query(Scan).all():
        predicted = class_for_area(scan.area_mm2 or 0, categories)
        if scan.predicted_class != predicted:
            scan.predicted_class = predicted
            updated += 1
    return updated


def migrate_study_classes(db: Session) -> None:
    """Keep only the five study classes and clear actual labels that are no longer valid."""
    db.flush()
    official = list(DEFAULT_CATEGORIES)
    names = [item["name"] for item in official]
    allowed = set(names)
    rows = list(db.query(Category).all())
    existing = {row.name: float(row.min_area) for row in rows}
    colors = {row.name: row.color for row in rows}
    mins = [existing.get(item["name"], float(item["min_area"])) for item in official]
    extra_index = names.index("Extra Large")
    if "Extra Large" not in existing:
        large = existing.get("Large", float(official[2]["min_area"]))
        jumbo = existing.get("Jumbo", float(official[4]["min_area"]))
        midpoint = round((large + jumbo) / 2, 2)
        if large < midpoint < jumbo:
            mins[extra_index] = midpoint
    increasing = all(mins[index] < mins[index + 1] for index in range(len(mins) - 1))
    current_names = [row.name for row in sorted(rows, key=lambda row: (row.sort_order, row.min_area))]
    needs_rebuild = current_names != names or len(rows) != len(official)
    needs_rebuild = needs_rebuild or any(colors.get(item["name"]) != item["color"] for item in official)
    if needs_rebuild:
        db.query(Category).delete()
        db.flush()
        for index, item in enumerate(official):
            db.add(
                Category(
                    name=item["name"],
                    min_area=mins[index] if increasing else item["min_area"],
                    color=item["color"],
                    sort_order=index,
                )
            )
        db.flush()
        db.expire_all()
    categories = get_categories(db)
    predicted_changed = 0
    actual_cleared = 0
    for scan in db.query(Scan).all():
        predicted = class_for_area(scan.area_mm2 or 0, categories)
        if scan.predicted_class != predicted:
            scan.predicted_class = predicted
            predicted_changed += 1
        if scan.actual_class is not None and scan.actual_class not in allowed:
            scan.actual_class = None
            actual_cleared += 1
    if predicted_changed or actual_cleared:
        add_log(
            db,
            "INFO",
            "Size classes are now Small, Medium, Large, Extra Large and Jumbo. "
            f"Recomputed {predicted_changed} predicted classes and cleared {actual_cleared} "
            "actual classes that are outside the study.",
        )
