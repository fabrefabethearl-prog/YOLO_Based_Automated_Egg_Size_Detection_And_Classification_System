"""Turn one folder of photos per size class into a YOLO detection dataset."""

from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
from datetime import datetime, timezone
from pathlib import Path

from backend.paths import IMAGE_SUFFIXES, dataset_dir
from backend.services.images import MAX_BYTES, ImageError, decode_image

MIN_PER_CLASS = 3
MAX_IMAGES = 5000
SPLITS = ("train", "val", "test")

# Folder spellings collapse onto these five keys. The model keeps the
# official names so Live scan and the ESP32 path can still read the class.
CLASSES = (
    {"id": 0, "key": "small", "name": "Small", "slug": "small"},
    {"id": 1, "key": "medium", "name": "Medium", "slug": "medium"},
    {"id": 2, "key": "large", "name": "Large", "slug": "large"},
    {"id": 3, "key": "extra large", "name": "Extra Large", "slug": "extra_large"},
    {"id": 4, "key": "jumbo", "name": "Jumbo", "slug": "jumbo"},
)
_BY_KEY = {item["key"]: item for item in CLASSES}


class DatasetRejected(ValueError):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def normalize_key(value: str) -> str | None:
    text = " ".join(str(value or "").strip().lower().replace("-", " ").replace("_", " ").split())
    compact = text.replace(" ", "")
    table = {
        "small": "small",
        "medium": "medium",
        "large": "large",
        "extra large": "extra large",
        "extralarge": "extra large",
        "jumbo": "jumbo",
    }
    return table.get(text) or table.get(compact)


def incoming_status() -> dict:
    counts = {spec["key"]: _count_incoming(spec["slug"]) for spec in CLASSES}
    ready = sum(1 for count in counts.values() if count >= MIN_PER_CLASS)
    missing = [spec["name"] for spec in CLASSES if counts[spec["key"]] == 0]
    return {
        "valid": ready == 5 and not missing,
        "classes": counts,
        "total_images": sum(counts.values()),
        "ready_classes": ready,
        "required": 5,
        "missing": missing,
        "minimum_per_class": MIN_PER_CLASS,
    }


def save_class(class_name: str, folder_name: str, entries: list[tuple[str, bytes]]) -> dict:
    slot = normalize_key(class_name)
    raw_folder = str(folder_name or "").strip()
    folder = normalize_key(raw_folder) if raw_folder else None
    if raw_folder and folder is None:
        raise DatasetRejected(
            f'This folder is named "{raw_folder}". Use small, medium, large, extra large, or jumbo.'
        )
    if slot is None and folder is None:
        raise DatasetRejected("Use a folder named small, medium, large, extra large, or jumbo.")
    if slot and folder and slot != folder:
        raise DatasetRejected(
            f'This folder is named "{folder_name}". Upload it on the {_BY_KEY[folder]["name"]} row.'
        )
    spec = _BY_KEY[slot or folder]
    images, errors = _read_images(entries, spec)
    if errors:
        return _status_with(spec, False, errors[0], errors)
    if len(images) < MIN_PER_CLASS:
        _write_incoming(spec, images)
        message = f'{spec["name"]} needs at least {MIN_PER_CLASS} images. Found {len(images)}.'
        return _status_with(spec, False, message, [message])
    _write_incoming(spec, images)
    return _status_with(spec, True, f'{spec["name"]}: {len(images)} images.', [])


def prepare_incoming(*, train_pct: int = 70, val_pct: int = 20, test_pct: int = 10, seed: int = 42) -> dict:
    ratios = _ratios(train_pct, val_pct, test_pct)
    if seed < 0 or seed > 2_147_483_647:
        raise DatasetRejected("Seed must be a whole number from 0 to 2147483647.")
    status = incoming_status()
    errors: list[str] = []
    for name in status["missing"]:
        errors.append(f"Missing class: {name}. Please upload the {name} dataset.")
    for spec in CLASSES:
        count = status["classes"][spec["key"]]
        if 0 < count < MIN_PER_CLASS:
            errors.append(f'{spec["name"]} needs at least {MIN_PER_CLASS} images. Found {count}.')
    if not errors:
        _decoded, found = _box_incoming()
        errors.extend(found)
    else:
        _decoded = {spec["key"]: [] for spec in CLASSES}
    splits = {"train": 0, "val": 0, "test": 0}
    wrote = False
    if not errors:
        _fit_lengths(_decoded)
        splits = _write_dataset(_decoded, ratios, seed, train_pct, val_pct, test_pct)
        wrote = True
    steps = _steps(status, errors, wrote)
    ready = wrote and not errors
    return {
        "valid": ready,
        "ready": ready,
        "status": "Ready for training." if ready else _failure_message(errors),
        "task": "YOLO Detection",
        "percent": _percent(steps),
        "steps": steps,
        "errors": errors,
        "classes": status["classes"],
        "total_images": status["total_images"],
        "splits": splits,
        "seed": seed,
        "annotation": (
            "YOLO detection. The folder name is the size class. "
            "The oval is drawn around the egg, and the label uses that oval's bounding rectangle. "
            "Length is the longer diameter and width is the shorter diameter."
        ),
    }


def dataset_is_ready() -> bool:
    info_path = dataset_dir() / "dataset_info.json"
    if not info_path.is_file():
        return False
    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not info.get("ready"):
        return False
    root = dataset_dir()
    if not (root / "lengths.csv").is_file():
        return False
    for split in ("train", "val"):
        folder = root / "images" / split
        if not folder.is_dir():
            return False
        names = [path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES]
        if not names:
            return False
    return True


def _status_with(spec: dict, class_valid: bool, message: str, errors: list[str]) -> dict:
    body = incoming_status()
    body["class_valid"] = class_valid
    body["class_key"] = spec["key"]
    body["class_name"] = spec["name"]
    body["message"] = message
    body["errors"] = errors
    return body


def clear_incoming(class_name: str) -> dict:
    """Remove the uploaded photos for one size class."""
    key = normalize_key(class_name)
    spec = _BY_KEY.get(key) if key else None
    if spec is None:
        raise DatasetRejected("Use small, medium, large, extra large, or jumbo.")
    folder = _incoming_dir(spec["slug"])
    removed = 0
    if folder.is_dir():
        for path in list(folder.iterdir()):
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
                path.unlink()
                removed += 1
    from backend.services.annotations import drop_missing

    drop_missing(spec)
    body = incoming_status()
    body["deleted"] = removed
    body["class_key"] = spec["key"]
    body["class_name"] = spec["name"]
    if removed == 1:
        body["message"] = f"Deleted 1 {spec['name']} image."
    else:
        body["message"] = f"Deleted {removed} {spec['name']} images."
    return body


def _incoming_dir(slug: str) -> Path:
    return dataset_dir() / "incoming" / slug


def _count_incoming(slug: str) -> int:
    folder = _incoming_dir(slug)
    if not folder.is_dir():
        return 0
    return sum(1 for path in folder.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES)


def _read_images(entries: list[tuple[str, bytes]], spec: dict):
    images = []
    errors = []
    seen: dict[str, str] = {}
    if not entries:
        return images, [f'Choose the folder named {spec["key"]}.']
    if len(entries) > MAX_IMAGES:
        return images, [f"Upload {MAX_IMAGES} images or fewer."]
    for raw_name, raw in entries:
        name = Path(str(raw_name).replace("\\", "/")).name
        if not name or name.startswith("."):
            continue
        suffix = Path(name).suffix.lower()
        if suffix not in IMAGE_SUFFIXES:
            errors.append(f"{name} is not a supported image.")
            continue
        if len(raw) > MAX_BYTES:
            errors.append(f"{name} is larger than 10 MB.")
            continue
        try:
            decode_image(raw)
        except ImageError:
            errors.append(f"{name} is corrupted.")
            continue
        digest = hashlib.sha256(raw).hexdigest()
        if digest in seen:
            errors.append(f"{name} duplicates {seen[digest]}.")
            continue
        seen[digest] = name
        images.append({"name": name, "suffix": suffix, "raw": raw})
    if not images and not errors:
        errors.append(f'{spec["name"]} contains no images.')
    return images, errors


def _write_incoming(spec: dict, images: list[dict]) -> None:
    folder = _incoming_dir(spec["slug"])
    if folder.exists():
        for path in folder.iterdir():
            if path.is_file():
                path.unlink()
    folder.mkdir(parents=True, exist_ok=True)
    for index, image in enumerate(images, start=1):
        (folder / f"{spec['slug']}_{index:04d}{image['suffix']}").write_bytes(image["raw"])


def _box_incoming() -> tuple[dict[str, list[dict]], list[str]]:
    from backend.services.annotations import boxes_for_incoming

    decoded = {spec["key"]: [] for spec in CLASSES}
    errors = []
    boxes = boxes_for_incoming()
    seen: dict[str, str] = {}
    for spec in CLASSES:
        folder = _incoming_dir(spec["slug"])
        paths = sorted(path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES)
        for path in paths:
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if digest in seen:
                errors.append(f"{path.name} duplicates {seen[digest]}.")
                continue
            seen[digest] = path.name
            try:
                image = decode_image(raw)
            except ImageError:
                errors.append(f"{path.name} is corrupted.")
                continue
            key = f"{spec['slug']}/{path.name}"
            mark = boxes.get(key)
            ovals = [] if mark is None else mark.get("ovals") or []
            if not ovals:
                errors.append(f"{spec['name']} → {path.name} missing oval annotation")
                continue
            label_lines = []
            eggs = []
            rejected = False
            for oval in ovals:
                line = _yolo_line(oval["box"], image.shape[1], image.shape[0])
                if line is None:
                    rejected = True
                    break
                label_lines.append(f"{spec['id']} {line}")
                eggs.append(
                    {
                        "length_mm": oval["length_mm"],
                        "width_mm": oval["width_mm"],
                        "length_px": oval["length_px"],
                        "width_px": oval["width_px"],
                        "area_px": oval["area_px"],
                    }
                )
            if rejected or not label_lines:
                errors.append(f"{spec['name']} → {path.name} box does not fit the image.")
                continue
            decoded[spec["key"]].append(
                {
                    "suffix": path.suffix.lower(),
                    "raw": raw,
                    "label": "\n".join(label_lines) + "\n",
                    "class_id": spec["id"],
                    "class_name": spec["name"],
                    "source": path.name,
                    "length_mm": eggs[0]["length_mm"],
                    "width_mm": eggs[0]["width_mm"],
                    "length_px": eggs[0]["length_px"],
                    "width_px": eggs[0]["width_px"],
                    "eggs": eggs,
                }
            )
        if len(decoded[spec["key"]]) < MIN_PER_CLASS and not any(spec["name"] in item for item in errors):
            errors.append(
                f'{spec["name"]} needs at least {MIN_PER_CLASS} images with an oval. Found {len(decoded[spec["key"]])}.'
            )
    return decoded, errors


def _yolo_line(box: tuple[int, int, int, int] | None, width: int, height: int) -> str | None:
    if box is None or width < 2 or height < 2:
        return None
    x, y, box_w, box_h = box
    if box_w < 2 or box_h < 2:
        return None
    cx = (x + box_w / 2) / width
    cy = (y + box_h / 2) / height
    nw = box_w / width
    nh = box_h / height
    if any(value <= 0 or value > 1 for value in (cx, cy, nw, nh)):
        return None
    return f"{cx:.6f} {cy:.6f} {nw:.6f} {nh:.6f}"


def _ratios(train_pct: int, val_pct: int, test_pct: int) -> tuple[float, float, float]:
    values = {"Train": train_pct, "Validation": val_pct, "Test": test_pct}
    for label, value in values.items():
        if isinstance(value, bool) or not isinstance(value, int):
            raise DatasetRejected(f"{label} percent must be a whole number.")
        if value < 0 or value > 100:
            raise DatasetRejected(f"{label} percent must be from 0 to 100.")
    if train_pct < 1 or val_pct < 1:
        raise DatasetRejected("Train and validation each need at least 1%.")
    if train_pct + val_pct + test_pct != 100:
        raise DatasetRejected("Train, validation, and test percents must add up to 100.")
    return train_pct / 100, val_pct / 100, test_pct / 100


def _allocate(count: int, ratios: tuple[float, float, float]) -> tuple[int, int, int]:
    raw = [count * ratio for ratio in ratios]
    floors = [int(value) for value in raw]
    leftover = count - sum(floors)
    order = sorted(range(3), key=lambda index: (raw[index] - floors[index], -index), reverse=True)
    for index in range(leftover):
        floors[order[index % 3]] += 1
    train, val, test = floors
    if ratios[2] == 0:
        train += test
        test = 0
    if val == 0 and train > 1:
        train -= 1
        val += 1
    if train == 0 and val > 1:
        val -= 1
        train += 1
    return train, val, test


def _write_dataset(decoded, ratios, seed, train_pct, val_pct, test_pct) -> dict:
    rng = random.Random(seed)
    staged = {split: [] for split in SPLITS}
    assignments = []
    for spec in CLASSES:
        items = list(decoded[spec["key"]])
        rng.shuffle(items)
        train_n, val_n, test_n = _allocate(len(items), ratios)
        if train_n < 1 or val_n < 1:
            raise DatasetRejected(f'{spec["name"]} does not have enough images for this split.')
        bounds = (
            ("train", 0, train_n),
            ("val", train_n, train_n + val_n),
            ("test", train_n + val_n, train_n + val_n + test_n),
        )
        cursor = 0
        for split, start, end in bounds:
            for item in items[start:end]:
                cursor += 1
                stem = f"{spec['slug']}_{split}_{cursor:04d}"
                staged[split].append({**item, "stem": stem})
                assignments.append({"source": item["source"], "class_key": spec["key"], "split": split, "file": stem})
    root = dataset_dir()
    for kind in ("images", "labels"):
        for split in SPLITS:
            folder = root / kind / split
            folder.mkdir(parents=True, exist_ok=True)
            for path in folder.iterdir():
                if path.is_file() and path.name != ".gitkeep":
                    path.unlink()
    for split, items in staged.items():
        for item in items:
            (root / "images" / split / f"{item['stem']}{item['suffix']}").write_bytes(item["raw"])
            (root / "labels" / split / f"{item['stem']}.txt").write_text(
                item["label"],
                encoding="utf-8",
            )
    (root / "data.yaml").write_text(_render_yaml(), encoding="utf-8")
    _write_lengths(root, staged)
    split_totals = {split: len(items) for split, items in staged.items()}
    info = {
        "ready": True,
        "task": "YOLO Detection",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "seed": seed,
        "split_pct": {"train": train_pct, "val": val_pct, "test": test_pct},
        "classes": [
            {"id": spec["id"], "key": spec["key"], "name": spec["name"], "images": len(decoded[spec["key"]])}
            for spec in CLASSES
        ],
        "mapping": {str(spec["id"]): spec["key"] for spec in CLASSES},
        "splits": split_totals,
        "files": assignments,
        "annotation": "oval drawn around the egg; class id from the folder name",
    }
    (root / "dataset_info.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    (root / "README.md").write_text(
        "# SmartEgg dataset\n\n"
        "YOLO detection. Each label is `class_id center_x center_y width height`.\n"
        "The class id comes from the folder name. The oval is drawn around the egg.\n"
        "Length is the longer diameter and width is the shorter diameter.\n\n"
        + "\n".join(f"- {item['id']}: {item['key']} ({item['images']} images)" for item in info["classes"])
        + "\n",
        encoding="utf-8",
    )
    return split_totals


def _write_lengths(root: Path, staged: dict) -> None:
    lines = ["image,class,length_mm,width_mm,split"]
    for split, items in staged.items():
        for item in items:
            name = f"{item['stem']}{item['suffix']}"
            eggs = item.get("eggs") or []
            for egg in eggs:
                length_mm = "" if egg["length_mm"] is None else egg["length_mm"]
                width_mm = "" if egg["width_mm"] is None else egg["width_mm"]
                lines.append(
                    f"{name},{item.get('class_name', '')},{length_mm},{width_mm},{split}"
                )
    (root / "lengths.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fit_lengths(decoded: dict) -> None:
    from backend.services.length_model import save_length_model

    samples = []
    summary = {}
    for spec in CLASSES:
        values = []
        invalid = 0
        missing = 0
        source_files = set()
        annotation_count = 0
        images_with_measurements = set()
        records = []
        for item in decoded[spec["key"]]:
            item_values = []
            for egg_index, egg in enumerate(item.get("eggs") or [], start=1):
                annotation_count += 1
                length_value = egg.get("length_mm")
                width_value = egg.get("width_mm")
                if length_value is None and width_value is None:
                    missing += 1
                    continue
                try:
                    length = float(length_value)
                    width = float(width_value)
                except (TypeError, ValueError):
                    invalid += 1
                    continue
                if not math.isfinite(length) or not math.isfinite(width) or length <= 0 or width <= 0:
                    invalid += 1
                    continue
                area = length * width
                if not math.isfinite(area):
                    invalid += 1
                    continue
                samples.append((spec["name"], area))
                values.append((length, width))
                item_values.append(area)
                records.append({
                    "image": item["source"],
                    "egg": egg_index,
                    "length_mm": length,
                    "width_mm": width,
                    "area_mm2": area,
                })
            if item_values:
                images_with_measurements.add(item["source"])
                source_files.add(item["source"])
        summary[spec["name"]] = {
            "images": len(decoded[spec["key"]]),
            "annotations": annotation_count,
            "valid": len(values),
            "invalid": invalid,
            "missing": missing,
            "images_with_measurements": len(images_with_measurements),
            "source_files": sorted(source_files),
            "records": records,
            "median_length_mm": statistics.median(value[0] for value in values) if values else None,
            "median_width_mm": statistics.median(value[1] for value in values) if values else None,
            "length_range_mm": (min(value[0] for value in values), max(value[0] for value in values)) if values else None,
            "width_range_mm": (min(value[1] for value in values), max(value[1] for value in values)) if values else None,
        }
    represented = {name for name, _area in samples}
    if represented != {spec["name"] for spec in CLASSES}:
        return
    try:
        save_length_model(samples, diagnostic_summary=summary)
    except ValueError as exc:
        raise DatasetRejected(str(exc)) from exc


def _render_yaml() -> str:
    lines = [
        "# Generated from the uploaded class folders.",
        "nc: 5",
        "path: .",
        "train: images/train",
        "val: images/val",
        "test: images/test",
        "",
        "names:",
    ]
    for spec in CLASSES:
        lines.append(f"  {spec['id']}: {spec['name']}")
    return "\n".join(lines) + "\n"


def _steps(status: dict, errors: list[str], wrote: bool) -> list[dict]:
    five = status["ready_classes"] == 5 and not status["missing"]
    steps = [
        {"ok": True, "label": "Datasets uploaded"},
        {"ok": five, "label": f"{status['ready_classes']} / 5 classes ready"},
        {"ok": status["total_images"] > 0, "label": f"{status['total_images']} images detected"},
        {"ok": not errors, "label": "Dataset validated" if not errors else "Dataset validation failed"},
        {"ok": wrote, "label": "Classes generated"},
        {"ok": wrote, "label": "Train/validation/test split completed"},
        {"ok": wrote, "label": "YOLO configuration generated"},
        {"ok": wrote, "label": "Dataset package created"},
    ]
    for error in errors:
        steps.append({"ok": False, "label": error})
    return steps


def _percent(steps: list[dict]) -> int:
    pipeline = steps[:8]
    done = sum(1 for step in pipeline if step["ok"])
    return int(round(100 * done / len(pipeline)))


def _failure_message(errors: list[str]) -> str:
    missing = next((item for item in errors if item.startswith("Missing class:")), "")
    if missing:
        return missing
    if errors:
        return errors[0]
    return "Please fix the dataset before training."
