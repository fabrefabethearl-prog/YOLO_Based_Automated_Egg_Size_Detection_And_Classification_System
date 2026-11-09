"""Accuracy, confusion, and benchmark summaries."""

from __future__ import annotations

import statistics
from typing import Sequence


def classification_report(rows: Sequence[tuple[str, str]], labels: Sequence[str], *, closed: bool = False) -> dict:
    ordered = list(labels)
    if not closed:
        seen = []
        for actual, predicted in rows:
            for name in (actual, predicted):
                if name not in ordered and name not in seen:
                    seen.append(name)
        ordered.extend(seen)
    index = {name: i for i, name in enumerate(ordered)}
    size = len(ordered)
    matrix = [[0 for _ in range(size)] for _ in range(size)]
    evaluated_rows = []
    for actual, predicted in rows:
        if actual not in index or predicted not in index:
            continue
        evaluated_rows.append((actual, predicted))
        matrix[index[actual]][index[predicted]] += 1

    per_class = []
    f1_values = []
    for i, name in enumerate(ordered):
        true_positive = matrix[i][i]
        support = sum(matrix[i])
        predicted_count = sum(matrix[row][i] for row in range(size))
        false_positive = predicted_count - true_positive
        false_negative = support - true_positive
        precision = true_positive / predicted_count if predicted_count else None
        recall = true_positive / support if support else None
        f1_denominator = 2 * true_positive + false_positive + false_negative
        f1 = 2 * true_positive / f1_denominator if f1_denominator else None
        correct = true_positive
        incorrect = false_negative
        per_class.append(
            {
                "name": name,
                "precision": precision,
                "recall": recall,
                "f1": f1,
                "support": support,
                "predicted_count": predicted_count,
                "true_positive": true_positive,
                "false_positive": false_positive,
                "false_negative": false_negative,
                "correct": correct,
                "incorrect": incorrect,
            }
        )

    total = len(evaluated_rows)
    accuracy = sum(matrix[i][i] for i in range(size)) / total if total else None

    def aggregate(metric: str, *, weighted: bool) -> float | None:
        candidates = [row for row in per_class if row["support"] > 0 and row[metric] is not None]
        if not candidates:
            return None
        if weighted:
            weight = sum(row["support"] for row in candidates)
            return sum(row[metric] * row["support"] for row in candidates) / weight if weight else None
        return sum(row[metric] for row in candidates) / len(candidates)

    return {
        "accuracy": accuracy,
        "macro_precision": aggregate("precision", weighted=False),
        "macro_recall": aggregate("recall", weighted=False),
        "macro_f1": aggregate("f1", weighted=False),
        "support_adjusted_precision": aggregate("precision", weighted=True),
        "support_adjusted_recall": aggregate("recall", weighted=True),
        "support_adjusted_f1": aggregate("f1", weighted=True),
        "correct_count": sum(matrix[i][i] for i in range(size)),
        "incorrect_count": total - sum(matrix[i][i] for i in range(size)),
        "evaluated_count": total,
        "ignored_count": len(rows) - total,
        "per_class": per_class,
        "labels": ordered,
        "matrix": matrix,
    }


def benchmark_summary(trials: Sequence) -> dict:
    if not trials:
        return {
            "trial_count": 0,
            "eggs": 0,
            "time_saved_pct": None,
            "per_egg_manual_s": None,
            "per_egg_eggscan_s": None,
            "manual_accuracy": None,
            "eggscan_accuracy": None,
            "accuracy_gain": None,
            "stdev_time_saved_pct": None,
        }

    eggs = sum(trial.eggs for trial in trials)
    manual_time = sum(trial.manual_time_s for trial in trials)
    eggscan_time = sum(trial.eggscan_time_s for trial in trials)
    manual_correct = sum(trial.manual_correct for trial in trials)
    eggscan_correct = sum(trial.eggscan_correct for trial in trials)

    time_saved_pct = ((manual_time - eggscan_time) / manual_time * 100) if manual_time > 0 else None
    per_egg_manual = (manual_time / eggs) if eggs else None
    per_egg_eggscan = (eggscan_time / eggs) if eggs else None
    manual_accuracy = (manual_correct / eggs) if eggs else None
    eggscan_accuracy = (eggscan_correct / eggs) if eggs else None
    accuracy_gain = (
        eggscan_accuracy - manual_accuracy
        if eggscan_accuracy is not None and manual_accuracy is not None
        else None
    )

    saved_rates = []
    for trial in trials:
        if trial.manual_time_s > 0:
            saved_rates.append((trial.manual_time_s - trial.eggscan_time_s) / trial.manual_time_s * 100)
    stdev = statistics.stdev(saved_rates) if len(saved_rates) >= 2 else None

    return {
        "trial_count": len(list(trials)),
        "eggs": eggs,
        "time_saved_pct": time_saved_pct,
        "per_egg_manual_s": per_egg_manual,
        "per_egg_eggscan_s": per_egg_eggscan,
        "manual_accuracy": manual_accuracy,
        "eggscan_accuracy": eggscan_accuracy,
        "accuracy_gain": accuracy_gain,
        "stdev_time_saved_pct": stdev,
    }
