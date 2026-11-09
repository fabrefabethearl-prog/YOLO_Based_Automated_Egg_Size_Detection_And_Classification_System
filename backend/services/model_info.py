"""Active model file and training summary. Numbers come from files on disk."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from backend.paths import ML_RUNS, size_path
from backend.services.detector import detector


def _iso(stamp: datetime) -> str:
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def active_model() -> dict:
    path = size_path()
    usable = path.is_file() and path.stat().st_size >= 1000 and not detector.mock
    if not usable:
        return {
            "name": "OpenCV contour",
            "version": "mock",
            "mode": "MOCK",
            "ready": False,
            "modified_at": None,
        }
    stat = path.stat()
    stamp = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    metadata = {}
    metadata_path = path.parent / "metadata.json"
    if metadata_path.is_file():
        try:
            saved = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                metadata = saved
        except (OSError, json.JSONDecodeError):
            metadata = {}
    if not detector.is_current():
        loaded_signature = detector.loaded_signature
        loaded_date = stamp.strftime("%Y%m%d")
        if loaded_signature:
            loaded_date = datetime.fromtimestamp(loaded_signature[1] / 1_000_000_000, tz=timezone.utc).strftime("%Y%m%d")
        return {
            "name": path.name,
            "version": f"{path.name}@loaded-{loaded_date}",
            "mode": "STALE",
            "ready": False,
            "modified_at": _iso(stamp),
            "error": "The checkpoint on disk differs from the model loaded by the inference service.",
        }
    return {
        "name": path.name,
        "version": f"{path.name}@{stat.st_mtime_ns:x}-{stat.st_size}",
        "mode": "REAL",
        "ready": True,
        "modified_at": _iso(stamp),
        "trained_at": metadata.get("trained_at"),
        "metrics": metadata.get("metrics") if isinstance(metadata.get("metrics"), dict) else None,
    }

def _newest(paths: list[Path]) -> Path | None:
    files = [path for path in paths if path.is_file()]
    if not files:
        return None
    return max(files, key=lambda item: item.stat().st_mtime)


def training_summary() -> dict | None:
    if not ML_RUNS.exists():
        return None
    metrics_path = _newest(list(ML_RUNS.rglob("metrics.json")))
    if metrics_path is not None:
        try:
            payload = json.loads(metrics_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if isinstance(payload, dict) and ("map50" in payload or "map50_95" in payload):
            stamp = datetime.fromtimestamp(metrics_path.stat().st_mtime, tz=timezone.utc)
            return {
                "map50": _number(payload.get("map50")),
                "map50_95": _number(payload.get("map50_95")),
                "epochs": _number(payload.get("epochs")),
                "date": payload.get("date") or _iso(stamp),
                "source": str(payload.get("source") or metrics_path.as_posix()),
                "evaluation_split": payload.get("evaluation_split"),
                "evaluation_images": _number(payload.get("evaluation_images")),
            }
    return None


def _number(value):
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number
