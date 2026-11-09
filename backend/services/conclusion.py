"""Benchmark sentence computed from saved trials."""

from __future__ import annotations

from backend.services.metrics import benchmark_summary


def benchmark_conclusion(trials) -> dict:
    count = len(list(trials))
    if count < 3:
        noun = "trial" if count == 1 else "trials"
        verb = "is" if count == 1 else "are"
        return {
            "ready": False,
            "trial_count": count,
            "text": (
                f"A conclusion needs at least 3 trials. There {verb} {count} saved {noun}."
            ),
        }

    summary = benchmark_summary(trials)
    eggs = summary["eggs"]
    per_manual = summary["per_egg_manual_s"]
    per_eggscan = summary["per_egg_eggscan_s"]
    gain = summary["accuracy_gain"]
    spread = summary["stdev_time_saved_pct"]
    if (
        not eggs
        or per_manual is None
        or per_eggscan is None
        or per_manual <= 0
        or gain is None
    ):
        return {
            "ready": False,
            "trial_count": count,
            "text": (
                f"Across {count} trials the stored times or scores are not enough "
                "to compare EggScan with manual sorting."
            ),
        }

    faster = (per_manual - per_eggscan) / per_manual * 100
    points = gain * 100
    speed = (
        f"{faster:.1f}% faster per egg"
        if faster >= 0
        else f"{abs(faster):.1f}% slower per egg"
    )
    accuracy = (
        f"{points:.1f} percentage points more accurate"
        if points >= 0
        else f"{abs(points):.1f} percentage points less accurate"
    )
    spread_text = "SD n/a" if spread is None else f"SD {spread:.1f}"
    linked = any(getattr(trial, "session_id", None) for trial in trials)
    linked_note = (
        " Linked trials use the summed inference time of the verified scans in that session."
        if linked
        else ""
    )
    return {
        "ready": True,
        "trial_count": count,
        "text": (
            f"Across {count} trials of {eggs} eggs, EggScan was {speed} and {accuracy} "
            f"than manual sorting ({spread_text}).{linked_note}"
        ),
    }
