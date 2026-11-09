"""Overview and model-performance numbers."""

from __future__ import annotations

import csv
import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.auth import get_current_user
from backend.db import get_db
from backend.models import Scan
from backend.paths import ML_RUNS
from backend.services.detector import detector
from backend.services.metrics import classification_report
from backend.services.model_info import active_model, training_summary
from backend.services.serialize import isoformat, round_or_none, scan_dict
from backend.services.settings_store import get_categories

router = APIRouter(prefix="/api/stats", tags=["stats"], dependencies=[Depends(get_current_user)])


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        from datetime import timezone

        return value.replace(tzinfo=timezone.utc)
    return value


def _mean(values: list[float]) -> float | None:
    return float(statistics.fmean(values)) if values else None


def _median(values: list[float]) -> float | None:
    return float(statistics.median(values)) if values else None


def _size_rows(scans, attr: str) -> list[dict]:
    grouped: dict[str, list] = defaultdict(list)
    for scan in scans:
        name = getattr(scan, attr)
        if name:
            grouped[name].append(scan)
    rows = []
    for name, items in grouped.items():
        lengths = [_positive(getattr(scan, "length_mm", None)) for scan in items]
        widths = [_positive(getattr(scan, "width_mm", None)) for scan in items]
        areas = [_positive(getattr(scan, "area_mm2", None)) for scan in items]
        lengths = [value for value in lengths if value is not None]
        widths = [value for value in widths if value is not None]
        areas = [value for value in areas if value is not None]
        rows.append(
            {
                "name": name,
                "count": len(items),
                "length_count": len(lengths),
                "width_count": len(widths),
                "area_count": len(areas),
                "mean_length_mm": round_or_none(_mean(lengths), 2),
                "median_length_mm": round_or_none(_median(lengths), 2),
                "mean_width_mm": round_or_none(_mean(widths), 2),
                "median_width_mm": round_or_none(_median(widths), 2),
                "mean_area_mm2": round_or_none(_mean(areas), 2),
                "median_area_mm2": round_or_none(_median(areas), 2),
                "areas": [round(value, 2) for value in areas[:200]],
            }
        )
    return rows


def _positive(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _ordered_size(scans, attr: str, categories) -> list[dict]:
    found = {row["name"]: row for row in _size_rows(scans, attr)}
    ordered = []
    for cat in categories:
        if cat.name in found:
            ordered.append(found[cat.name])
            continue
        ordered.append(
            {
                "name": cat.name,
                "count": 0,
                "length_count": 0,
                "width_count": 0,
                "area_count": 0,
                "mean_length_mm": None,
                "median_length_mm": None,
                "mean_width_mm": None,
                "median_width_mm": None,
                "mean_area_mm2": None,
                "median_area_mm2": None,
                "areas": [],
            }
        )
    return ordered


def _repeatability(db: Session) -> dict:
    grouped: dict[str, list[float]] = defaultdict(list)
    for scan in db.query(Scan).filter(Scan.session_id.isnot(None), Scan.area_mm2.isnot(None)).all():
        grouped[scan.session_id].append(float(scan.area_mm2))
    spreads = [statistics.stdev(areas) for areas in grouped.values() if len(areas) >= 2]
    return {
        "sessions": len(spreads),
        "mean_stdev_area_mm2": round_or_none(float(statistics.fmean(spreads)) if spreads else None, 2),
    }


@router.get("/overview")
def overview(db: Session = Depends(get_db)):
    total = db.query(func.count(Scan.id)).scalar() or 0
    labelled = db.query(Scan).filter(Scan.actual_class.isnot(None)).all()
    correct = sum(1 for scan in labelled if scan.predicted_class == scan.actual_class)
    accuracy = (correct / len(labelled)) if labelled else None
    avg_ms = db.query(func.avg(Scan.inference_ms)).scalar()
    avg_confidence = db.query(func.avg(Scan.confidence)).scalar()

    today = datetime.now().astimezone().date()
    days = [today - timedelta(days=offset) for offset in range(13, -1, -1)]
    counts = {day.isoformat(): 0 for day in days}
    for (stamp,) in db.query(Scan.created_at).all():
        local_day = _aware(stamp).astimezone().date().isoformat()
        if local_day in counts:
            counts[local_day] += 1

    categories = get_categories(db)
    grouped = dict(
        db.query(Scan.predicted_class, func.count(Scan.id))
        .group_by(Scan.predicted_class)
        .all()
    )
    distribution = [
        {"name": cat.name, "count": int(grouped.get(cat.name, 0)), "color": cat.color}
        for cat in categories
    ]
    recent = db.query(Scan).order_by(Scan.created_at.desc(), Scan.id.desc()).limit(8).all()
    last = recent[0] if recent else None
    verified = len(labelled)
    return {
        "mock_mode": detector.mock,
        "total_scanned": total,
        "labelled_count": verified,
        "verified_count": verified,
        "unverified_count": total - verified,
        "miss_count": verified - correct,
        "accuracy": round_or_none(accuracy, 4),
        "avg_inference_ms": round_or_none(avg_ms, 2),
        "avg_confidence": round_or_none(avg_confidence, 4),
        "size_by_class": _ordered_size(db.query(Scan).all(), "predicted_class", categories),
        "repeatability": _repeatability(db),
        "last_scan_at": isoformat(last.created_at) if last else None,
        "model": active_model(),
        "per_day": [{"date": day, "count": counts[day]} for day in counts],
        "class_distribution": distribution,
        "recent_scans": [scan_dict(scan) for scan in recent],
    }


@router.get("/model")
def model_stats(db: Session = Depends(get_db)):
    labels = [cat.name for cat in get_categories(db)]
    label_set = set(labels)
    candidates = db.query(Scan).filter(Scan.is_mock.is_(False), Scan.actual_class.isnot(None)).all()
    labelled = [scan for scan in candidates if scan.actual_class in label_set and scan.predicted_class in label_set]
    report = classification_report(
        [(scan.actual_class, scan.predicted_class) for scan in labelled],
        labels,
        closed=True,
    )
    sample_size = len(labelled)
    confidences = [float(scan.confidence) for scan in labelled if scan.confidence is not None]
    per_class = []
    for row in report["per_class"]:
        per_class.append(
            {
                "name": row["name"],
                "precision": round_or_none(row["precision"], 4),
                "recall": round_or_none(row["recall"], 4),
                "f1": round_or_none(row["f1"], 4),
                "support": row["support"],
                "predicted_count": row["predicted_count"],
                "true_positive": row["true_positive"],
                "false_positive": row["false_positive"],
                "false_negative": row["false_negative"],
                "correct": row["correct"],
                "incorrect": row["incorrect"],
                "low_sample": 0 < row["support"] < 5,
            }
        )
    ignored_candidates = len(candidates) - len(labelled)
    return {
        "mock_mode": detector.mock,
        "labelled_count": sample_size,
        "sample_size": sample_size,
        "correct_count": report["correct_count"],
        "incorrect_count": report["incorrect_count"],
        "ignored_count": ignored_candidates,
        "small_sample": 0 < sample_size < 30,
        "small_sample_note": "Small sample: results are indicative only." if 0 < sample_size < 30 else None,
        "accuracy": round_or_none(report["accuracy"], 4),
        "macro_precision": round_or_none(report["macro_precision"], 4),
        "macro_recall": round_or_none(report["macro_recall"], 4),
        "macro_f1": round_or_none(report["macro_f1"], 4),
        "support_adjusted_precision": round_or_none(report["support_adjusted_precision"], 4),
        "support_adjusted_recall": round_or_none(report["support_adjusted_recall"], 4),
        "support_adjusted_f1": round_or_none(report["support_adjusted_f1"], 4),
        "avg_confidence": round_or_none(_mean(confidences), 4),
        "per_class": per_class,
        "class_balance": [
            {"name": row["name"], "count": row["support"], "low": row["support"] < 5}
            for row in per_class
        ],
        "confusion": {"labels": report["labels"], "matrix": report["matrix"]},
        "size_by_actual_class": _ordered_size(labelled, "actual_class", get_categories(db)),
        "training_curve": load_training_curve(),
        "training": training_summary(),
        "model": active_model(),
    }


def _report_payload(db: Session) -> dict:
    categories = get_categories(db)
    labels = [category.name for category in categories]
    label_set = set(labels)
    scans = db.query(Scan).order_by(Scan.created_at.asc(), Scan.id.asc()).all()
    candidates = [scan for scan in scans if not scan.is_mock and scan.actual_class is not None]
    evaluated = [
        scan for scan in candidates
        if scan.actual_class in label_set and scan.predicted_class in label_set
    ]
    metric = classification_report([(scan.actual_class, scan.predicted_class) for scan in evaluated], labels, closed=True)
    by_name = {row["name"]: row for row in metric["per_class"]}

    predictions = []
    actual_distribution = []
    for category in categories:
        matching = [scan for scan in scans if scan.predicted_class == category.name]
        metric_row = by_name.get(category.name, {})
        predictions.append({
            "name": category.name,
            "count": len(matching),
            "mock_count": sum(bool(scan.is_mock) for scan in matching),
            "real_count": sum(not scan.is_mock for scan in matching),
            "evaluated_count": metric_row.get("predicted_count", 0),
        })
        actual_distribution.append({"name": category.name, "count": metric_row.get("support", 0)})

    matrix = metric["matrix"]
    row_percentages = []
    for row in matrix:
        support = sum(row)
        row_percentages.append([round(value / support, 4) if support else None for value in row])

    measurements = _ordered_size(evaluated, "actual_class", categories)
    now = datetime.now(timezone.utc)
    period = {
        "from": isoformat(evaluated[0].created_at) if evaluated else None,
        "to": isoformat(evaluated[-1].created_at) if evaluated else None,
    }
    start_day = now.date() - timedelta(days=13)
    daily = { (start_day + timedelta(days=offset)).isoformat(): {"count": 0, "correct": 0, "incorrect": 0}
             for offset in range(14) }
    for scan in evaluated:
        stamp = _aware(scan.created_at).astimezone(timezone.utc).date().isoformat()
        if stamp in daily:
            daily[stamp]["count"] += 1
            daily[stamp]["correct" if scan.actual_class == scan.predicted_class else "incorrect"] += 1

    supports = [row["support"] for row in metric["per_class"] if row["support"]]
    missing_classes = [row["name"] for row in metric["per_class"] if row["support"] == 0]
    confusion = []
    for actual_index, row in enumerate(matrix):
        for predicted_index, count in enumerate(row):
            if actual_index != predicted_index and count:
                confusion.append({"actual": labels[actual_index], "predicted": labels[predicted_index], "count": count})
    confusion.sort(key=lambda item: (-item["count"], item["actual"], item["predicted"]))

    excluded_mock = sum(bool(scan.is_mock) for scan in scans)
    excluded_unverified = sum(not scan.is_mock and scan.actual_class is None for scan in scans)
    excluded_invalid = len(candidates) - len(evaluated)
    recommendations = []
    if not evaluated:
        recommendations.append("Verify real model scan records against known egg classes to create an evaluation set.")
    elif len(evaluated) < 30:
        recommendations.append(f"The evaluation contains {len(evaluated)} verified scan records; treat these results as preliminary and collect a broader, independently reviewed sample.")
    if missing_classes:
        recommendations.append("No verified evaluation samples are present for: " + ", ".join(missing_classes) + ".")
    if len(supports) > 1 and max(supports) >= 2 * min(supports):
        recommendations.append(f"Verified class support is uneven (largest class {max(supports)} samples; smallest represented class {min(supports)}); add samples to improve class balance.")
    if confusion:
        top = confusion[0]
        recommendations.append(f"Most frequent observed confusion: actual {top['actual']} predicted as {top['predicted']} ({top['count']} records).")

    return {
        "colors": {category.name: category.color for category in categories},
        "summary": {
            "total_scans": len(scans),
            "verified_count": metric["evaluated_count"],
            "correct_count": metric["correct_count"],
            "incorrect_count": metric["incorrect_count"],
            "accuracy": round_or_none(metric["accuracy"], 4),
            "represented_classes": sum(row["support"] > 0 for row in metric["per_class"]),
            "class_count": len(labels),
            "period": period,
            "refreshed_at": isoformat(now),
        },
        "evaluation": {
            "labels": labels,
            "accuracy": round_or_none(metric["accuracy"], 4),
            "macro_precision": round_or_none(metric["macro_precision"], 4),
            "macro_recall": round_or_none(metric["macro_recall"], 4),
            "macro_f1": round_or_none(metric["macro_f1"], 4),
            "support_adjusted_precision": round_or_none(metric["support_adjusted_precision"], 4),
            "support_adjusted_recall": round_or_none(metric["support_adjusted_recall"], 4),
            "support_adjusted_f1": round_or_none(metric["support_adjusted_f1"], 4),
            "per_class": metric["per_class"],
            "confusion": {"labels": labels, "matrix": matrix, "row_percentages": row_percentages},
            "actual_distribution": actual_distribution,
            "verified_prediction_distribution": [
                {"name": row["name"], "count": row["predicted_count"]} for row in metric["per_class"]
            ],
        },
        "measurements": measurements,
        "prediction_distribution": predictions,
        "daily": [{"date": day, **values} for day, values in daily.items()],
        "quality": {
            "excluded_mock_scans": excluded_mock,
            "excluded_unverified_scans": excluded_unverified,
            "excluded_invalid_label_scans": excluded_invalid,
            "unsupported_actual_classes": missing_classes,
            "support_ratio": round(max(supports) / min(supports), 2) if len(supports) > 1 else None,
            "frequent_confusions": confusion[:5],
            "recommendations": recommendations,
            "evaluation_unit": "One saved scan record. The database has no stable egg identifier, so repeated scans cannot be deduplicated.",
            "measurement_provenance": "Lengths, widths, and areas are stored millimetre estimates. Per-scan calibration snapshots are not stored, so historical calibration cannot be independently verified.",
            "metric_method": "Evaluation uses non-mock scan records with a recognized manually assigned actual class and recognized predicted class. Unverified, mock, or unsupported-label records are excluded from all classification metrics.",
        },
        "model": active_model(),
    }


@router.get("/report")
def report_data(db: Session = Depends(get_db)):
    return _report_payload(db)


def load_training_curve() -> dict | None:
    if not ML_RUNS.exists():
        return None
    csv_files = [path for path in ML_RUNS.rglob("results.csv") if path.is_file()]
    if not csv_files:
        return None
    path = max(csv_files, key=lambda item: item.stat().st_mtime)
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        headers = reader.fieldnames or []
    if not rows or not headers:
        return None

    def pick(*needles: str) -> str | None:
        for needle in needles:
            for header in headers:
                if needle in header:
                    return header
        return None

    epoch_key = pick("epoch")
    map50_key = pick("mAP50-95")
    map_key = None
    for header in headers:
        if "mAP50" in header and "95" not in header:
            map_key = header
            break
    train_loss_key = pick("train/box_loss")
    val_loss_key = pick("val/box_loss")

    def column(key: str | None) -> list[float | None]:
        if key is None:
            return [None for _ in rows]
        values = []
        for row in rows:
            raw = (row.get(key) or "").strip()
            if raw == "":
                values.append(None)
            else:
                try:
                    values.append(float(raw))
                except ValueError:
                    values.append(None)
        return values

    try:
        relative = path.relative_to(Path(__file__).resolve().parents[2])
        source = relative.as_posix()
    except ValueError:
        source = path.name
    return {
        "source": source,
        "epochs": column(epoch_key),
        "map50": column(map_key),
        "map50_95": column(map50_key),
        "train_box_loss": column(train_loss_key),
        "val_box_loss": column(val_loss_key),
    }
