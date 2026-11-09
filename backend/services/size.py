"""Egg size from a bounding box, and area bands for the size class."""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Sequence


def box_to_mm(width_px: float, height_px: float, pixels_per_mm: float) -> tuple[float, float]:
    """Return (length_mm, width_mm). Length is the longer side."""
    if pixels_per_mm <= 0:
        raise ValueError("pixels_per_mm must be positive.")
    longer = max(width_px, height_px) / pixels_per_mm
    shorter = min(width_px, height_px) / pixels_per_mm
    return longer, shorter


def area_mm2(length_mm: float, width_mm: float) -> float:
    return float(length_mm) * float(width_mm)


def class_for_area(area: float, categories: Sequence) -> str:
    if not categories:
        return "Unclassified"
    ordered = sorted(categories, key=lambda cat: (float(cat.min_area), int(cat.sort_order)))
    chosen = ordered[0].name
    for cat in ordered:
        if float(area) >= float(cat.min_area):
            chosen = cat.name
        else:
            break
    return chosen


OUT_OF_RANGE = "Out of range (outside Small to Jumbo)"


def assign_class(
    area: float,
    categories: Sequence,
    *,
    mode: str,
    mock: bool,
    class_name: str | None,
    model_class_count: int,
    out_of_range: str = "nearest",
) -> tuple[str | None, str]:
    """Return (class name or None, mode actually used). None means do not save."""
    named = (class_name or "").strip()
    official = [cat.name for cat in categories]
    if mode == "model" and not mock and model_class_count > 1 and named and named.lower() != "egg":
        if named not in official:
            return None, "model"
        return named, "model"
    ordered = sorted(categories, key=lambda cat: (float(cat.min_area), int(cat.sort_order)))
    if out_of_range == "reject" and ordered and float(area) < float(ordered[0].min_area):
        return None, "range"
    return class_for_area(area, categories), "dimensions"


def _median(values: Sequence[float]) -> float:
    return float(statistics.median(values))


def fit_size_bands(samples: Sequence[tuple[str, float]], categories: Sequence) -> dict:
    """Midpoints between class medians of area, keeping the study class order."""
    allowed = {cat.name for cat in categories}
    rows = [(name, float(area)) for name, area in samples if name in allowed and area is not None]
    if len(rows) < 10:
        raise ValueError("Need at least 10 verified eggs before fitting size bands.")
    grouped: dict[str, list[float]] = defaultdict(list)
    for name, area in rows:
        grouped[name].append(area)
    if sum(1 for cat in categories if grouped.get(cat.name)) < 2:
        raise ValueError("Need verified eggs in at least two size classes.")
    warnings = []
    fitted = []
    ordered = list(categories)
    for index, cat in enumerate(ordered):
        areas = grouped.get(cat.name) or []
        if areas and len(areas) < 5:
            warnings.append(
                f"{cat.name} has {len(areas)} verified eggs. Five or more gives a steadier band."
            )
        if index == 0:
            minimum = 0.0
        else:
            previous_areas = grouped.get(ordered[index - 1].name) or []
            if areas and previous_areas:
                minimum = round((_median(previous_areas) + _median(areas)) / 2, 2)
            else:
                minimum = round(fitted[-1]["min_area"] + 0.01, 2)
            if not areas:
                warnings.append(f"{cat.name} has no verified eggs, so its band stays just above the class before it.")
            if minimum <= fitted[-1]["min_area"]:
                minimum = round(fitted[-1]["min_area"] + 0.01, 2)
        fitted.append(
            {
                "name": cat.name,
                "color": cat.color,
                "min_area": minimum,
                "median_area_mm2": round(_median(areas), 2) if areas else None,
                "count": len(areas),
                "sort_order": index,
            }
        )
    return {"bands": fitted, "warnings": warnings, "sample_count": len(rows)}
