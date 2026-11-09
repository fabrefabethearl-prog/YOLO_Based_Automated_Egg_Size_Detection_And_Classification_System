"""Manual sorting versus EggScan."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.auth import get_current_user
from backend.db import get_db
from backend.models import BenchmarkTrial, Scan, User
from backend.schemas import TrialIn
from backend.services.audit import add_log
from backend.services.conclusion import benchmark_conclusion
from backend.services.metrics import benchmark_summary
from backend.services.serialize import round_or_none, trial_dict

router = APIRouter(prefix="/api/benchmark", tags=["benchmark"], dependencies=[Depends(get_current_user)])


def _summary_payload(trials: list[BenchmarkTrial]) -> dict:
    summary = benchmark_summary(trials)
    for key in (
        "time_saved_pct",
        "per_egg_manual_s",
        "per_egg_eggscan_s",
        "manual_accuracy",
        "eggscan_accuracy",
        "accuracy_gain",
        "stdev_time_saved_pct",
    ):
        digits = 4 if "accuracy" in key or key == "accuracy_gain" else 3
        summary[key] = round_or_none(summary[key], digits)
    return summary


@router.get("")
def list_trials(db: Session = Depends(get_db)):
    trials = db.query(BenchmarkTrial).order_by(BenchmarkTrial.trial_no.asc(), BenchmarkTrial.id.asc()).all()
    return {
        "summary": _summary_payload(trials),
        "conclusion": benchmark_conclusion(trials),
        "trials": [trial_dict(trial) for trial in trials],
    }


@router.post("")
def add_trial(
    body: TrialIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if body.trial_no is None:
        current = db.query(BenchmarkTrial.trial_no).order_by(BenchmarkTrial.trial_no.desc()).first()
        trial_no = 1 if current is None else int(current[0]) + 1
    else:
        trial_no = body.trial_no
    eggs = body.eggs
    eggscan_time_s = body.eggscan_time_s
    eggscan_correct = body.eggscan_correct
    session_id = body.session_id
    if session_id:
        scans = db.query(Scan).filter(Scan.session_id == session_id).all()
        if not scans:
            raise HTTPException(status_code=400, detail="That session has no saved scans.")
        verified = [scan for scan in scans if scan.actual_class]
        if not verified:
            raise HTTPException(
                status_code=400,
                detail="Choose an actual size class for the eggs in that session before linking it.",
            )
        eggs = len(verified)
        eggscan_time_s = round(sum(scan.inference_ms for scan in verified) / 1000, 3)
        eggscan_correct = sum(1 for scan in verified if scan.predicted_class == scan.actual_class)
        if body.manual_correct > eggs:
            raise HTTPException(
                status_code=400,
                detail="Manual correct cannot exceed the verified eggs in that session.",
            )
    trial = BenchmarkTrial(
        trial_no=trial_no,
        eggs=eggs,
        manual_time_s=body.manual_time_s,
        eggscan_time_s=eggscan_time_s,
        manual_correct=body.manual_correct,
        eggscan_correct=eggscan_correct,
        session_id=session_id,
    )
    db.add(trial)
    db.flush()
    add_log(db, "INFO", f"{user.email} recorded benchmark trial {trial.trial_no} ({trial.eggs} eggs).")
    db.commit()
    db.refresh(trial)
    return trial_dict(trial)


@router.delete("/{trial_id}")
def delete_trial(
    trial_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    trial = db.get(BenchmarkTrial, trial_id)
    if trial is None:
        raise HTTPException(status_code=404, detail="Trial not found.")
    number = trial.trial_no
    db.delete(trial)
    add_log(db, "INFO", f"{user.email} deleted benchmark trial {number}.")
    db.commit()
    return {"ok": True}
