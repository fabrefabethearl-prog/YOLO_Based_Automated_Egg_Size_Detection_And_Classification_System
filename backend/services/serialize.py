"""JSON helpers shared by the routers."""

from __future__ import annotations

from datetime import datetime, timezone

from backend.models import Scan


def isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def round_or_none(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def scan_dict(scan: Scan) -> dict:
    correct = None
    if scan.actual_class:
        correct = scan.predicted_class == scan.actual_class
    return {
        "id": scan.id,
        "created_at": isoformat(scan.created_at),
        "predicted_class": scan.predicted_class,
        "actual_class": scan.actual_class,
        "length_mm": round_or_none(scan.length_mm, 2),
        "width_mm": round_or_none(scan.width_mm, 2),
        "area_mm2": round_or_none(scan.area_mm2, 2),
        "confidence": round_or_none(scan.confidence, 4),
        "inference_ms": round_or_none(scan.inference_ms, 2),
        "is_mock": bool(scan.is_mock),
        "correct": correct,
        "operator_email": scan.operator_email,
        "model_version": scan.model_version,
        "session_id": scan.session_id,
    }


def category_dict(category) -> dict:
    return {
        "id": category.id,
        "name": category.name,
        "min_area": category.min_area,
        "color": category.color,
        "sort_order": category.sort_order,
    }


def trial_dict(trial) -> dict:
    manual_per = trial.manual_time_s / trial.eggs if trial.eggs else None
    eggscan_per = trial.eggscan_time_s / trial.eggs if trial.eggs else None
    saved = None
    if trial.manual_time_s > 0:
        saved = (trial.manual_time_s - trial.eggscan_time_s) / trial.manual_time_s * 100
    return {
        "id": trial.id,
        "trial_no": trial.trial_no,
        "eggs": trial.eggs,
        "manual_time_s": round_or_none(trial.manual_time_s, 3),
        "eggscan_time_s": round_or_none(trial.eggscan_time_s, 3),
        "manual_correct": trial.manual_correct,
        "eggscan_correct": trial.eggscan_correct,
        "created_at": isoformat(trial.created_at),
        "time_saved_pct": round_or_none(saved, 2),
        "per_egg_manual_s": round_or_none(manual_per, 3),
        "per_egg_eggscan_s": round_or_none(eggscan_per, 3),
        "manual_accuracy": round_or_none(trial.manual_correct / trial.eggs, 4) if trial.eggs else None,
        "eggscan_accuracy": round_or_none(trial.eggscan_correct / trial.eggs, 4) if trial.eggs else None,
        "session_id": trial.session_id,
    }
