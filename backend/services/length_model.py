"""Size class from a measured box. This is not a YOLO input.

YOLO finds the egg. This file turns the box area, length times width in
square millimetres, into Small, Medium, Large, Extra Large, or Jumbo.
The bands are the midpoints between the annotated areas of each class.
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

from backend.paths import data_dir
from backend.services.dataset_prep import CLASSES

MODEL_NAME = "length_model.json"


def model_path() -> Path:
    return data_dir() / MODEL_NAME


def fit_length_bands(samples: list[tuple[str, float]], diagnostic_summary: dict | None = None) -> dict:
    grouped: dict[str, list[float]] = {spec["name"]: [] for spec in CLASSES}
    for name, length in samples:
        if name not in grouped:
            raise ValueError(f"{name} is not one of the five size classes.")
        value = float(length)
        if value != value or value in (float("inf"), float("-inf")) or value <= 0:
            raise ValueError("Every annotated area must be a positive number of square millimetres.")
        grouped[name].append(value)
    missing = [name for name, values in grouped.items() if not values]
    if missing:
        raise ValueError("Missing box annotations for " + ", ".join(missing) + ".")
    medians = [float(statistics.median(grouped[spec["name"]])) for spec in CLASSES]
    violations = [
        (CLASSES[index], medians[index], grouped[CLASSES[index]["name"]],
         CLASSES[index + 1], medians[index + 1], grouped[CLASSES[index + 1]["name"]])
        for index in range(len(CLASSES) - 1)
        if medians[index] >= medians[index + 1]
    ]
    if violations:
        summary_lines = []
        diagnostic_summary = diagnostic_summary or {}
        for spec, median_area in zip(CLASSES, medians):
            details = diagnostic_summary.get(spec["name"], {})
            line = (
                f'{spec["name"]} (ID {spec["id"]}, folder {spec["slug"]}): '
                f'{details.get("images", 0)} images, {details.get("annotations", len(grouped[spec["name"]]))} annotations, '
                f'{details.get("valid", len(grouped[spec["name"]]))} valid, '
                f'{details.get("invalid", 0)} invalid, {details.get("missing", 0)} missing physical measurements'
            )
            if details.get("median_length_mm") is not None and details.get("median_width_mm") is not None:
                line += (
                    f'; median length {details["median_length_mm"]:.2f} mm, '
                    f'median width {details["median_width_mm"]:.2f} mm'
                )
            if details.get("length_range_mm") and details.get("width_range_mm"):
                line += (
                    f'; length range {details["length_range_mm"][0]:.2f}-'
                    f'{details["length_range_mm"][1]:.2f} mm, width range '
                    f'{details["width_range_mm"][0]:.2f}-'
                    f'{details["width_range_mm"][1]:.2f} mm'
                )
            area_count = len(grouped[spec["name"]])
            line += f'; median area {median_area:.2f} mm^2 (n={area_count}'
            if details.get("images_with_measurements") is not None:
                line += f', {details["images_with_measurements"]} measured images'
            line += ")."
            summary_lines.append(line)

        violation_lines = []
        affected_classes = set()
        for lower, lower_area, lower_values, upper, upper_area, upper_values in violations:
            difference = lower_area - upper_area
            violation_lines.append(
                f'{lower["name"]} median area {lower_area:.2f} mm^2 (n={len(lower_values)}) '
                f'must be below {upper["name"]} median area {upper_area:.2f} mm^2 (n={len(upper_values)}); '
                f'the inversion is {difference:.2f} mm^2.'
            )
            affected_classes.update((lower["name"], upper["name"]))

        source_lines = []
        measurement_lines = []
        for name in (spec["name"] for spec in CLASSES if spec["name"] in affected_classes):
            details = diagnostic_summary.get(name, {})
            files = details.get("source_files", [])
            if files:
                source_lines.append(f'{name} source images to review: {", ".join(files)}.')
            for record in details.get("records", []):
                measurement_lines.append(
                    f'{name}/{record["image"]} egg {record["egg"]}: '
                    f'length {record["length_mm"]:.2f} mm, '
                    f'width {record["width_mm"]:.2f} mm, '
                    f'area {record["area_mm2"]:.2f} mm^2.'
                )
        message = (
            "Annotated median areas are not strictly increasing from Small to Jumbo. "
            + " ".join(violation_lines)
            + " Class measurements (area is the median of each valid length x width product): "
            + " ".join(summary_lines)
            + (" " + " ".join(source_lines) if source_lines else "")
            + (" Individual affected-class measurements: " + " ".join(measurement_lines) if measurement_lines else "")
            + " Review the listed measurements, calibration, and folder labels. No source annotations were changed."
        )
        raise ValueError(message)
    minimums = [0.0]
    for index in range(1, 5):
        minimums.append(round((medians[index - 1] + medians[index]) / 2, 2))
    return {
        "feature": "area_mm2",
        "task": "size classifier",
        "note": "YOLO does not read the box size. This classifier uses length times width for each egg.",
        "classes": [spec["name"] for spec in CLASSES],
        "min_area_mm2": minimums,
        "median_area_mm2": [round(value, 2) for value in medians],
        "samples": len(samples),
        "trained_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def save_length_model(samples: list[tuple[str, float]], diagnostic_summary: dict | None = None) -> dict:
    payload = fit_length_bands(samples, diagnostic_summary=diagnostic_summary)
    path = model_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def load_length_model() -> dict | None:
    path = model_path()
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    classes = payload.get("classes")
    if classes != [spec["name"] for spec in CLASSES]:
        return None
    if payload.get("feature") == "area_mm2":
        minimums = payload.get("min_area_mm2")
    else:
        minimums = payload.get("min_length_mm")
    if not isinstance(minimums, list) or len(minimums) != 5:
        return None
    return payload


def class_for_measured_length(length_mm: float | None) -> str | None:
    """Classify from length only when an older length model is still on disk."""
    return _class_from_value(length_mm, "min_length_mm")


def class_for_measured_size(length_mm: float | None, width_mm: float | None = None) -> str | None:
    """Classify from length times width. Older length-only models still use length."""
    payload = load_length_model()
    if payload is None or length_mm is None:
        return None
    if payload.get("feature") == "area_mm2":
        if width_mm is None:
            return None
        return _class_from_value(float(length_mm) * float(width_mm), "min_area_mm2")
    return _class_from_value(length_mm, "min_length_mm")


def _class_from_value(value: float | None, key: str) -> str | None:
    if value is None:
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")) or number <= 0:
        return None
    payload = load_length_model()
    if payload is None:
        return None
    minimums = payload.get(key)
    if not isinstance(minimums, list):
        return None
    chosen = payload["classes"][0]
    for name, minimum in zip(payload["classes"], minimums):
        if number >= float(minimum):
            chosen = name
        else:
            break
    return chosen
