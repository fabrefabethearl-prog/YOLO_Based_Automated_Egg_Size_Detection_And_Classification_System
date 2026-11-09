"""Run ml/train.py and load the finished size model for the next scan."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

from backend.paths import ROOT, size_path
from backend.services.dataset_prep import CLASSES, dataset_is_ready

_lock = threading.Lock()
_thread: threading.Thread | None = None
_state = {
    "status": "idle",
    "message": "No training run yet.",
    "epoch": None,
    "epochs": 100,
    "percent": 0,
    "log": "",
    "error": None,
}
_EPOCH = re.compile(r"(?:Epoch\s+)?(\d+)\s*/\s*(\d+)")


class TrainingRejected(ValueError):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def training_status() -> dict:
    with _lock:
        body = dict(_state)
    body["ready"] = dataset_is_ready()
    if body["status"] == "done":
        body["model"] = _read_metadata()
    return body


def start_training() -> dict:
    global _thread
    if not dataset_is_ready():
        raise TrainingRejected("Process a valid dataset before training.")
    with _lock:
        if _state["status"] == "running":
            raise TrainingRejected("Training is already running.")
        _state.update(
            status="running",
            message="Training...",
            epoch=None,
            epochs=100,
            percent=0,
            log="",
            error=None,
        )
    _thread = threading.Thread(target=_run, name="eggscan-train", daemon=True)
    _thread.start()
    return training_status()


def wait_for_training(timeout: float = 30) -> dict:
    if _thread is not None:
        _thread.join(timeout)
    return training_status()


def reset_training_state() -> None:
    global _thread
    with _lock:
        _state.update(
            status="idle",
            message="No training run yet.",
            epoch=None,
            epochs=100,
            percent=0,
            log="",
            error=None,
        )
    _thread = None


def _run() -> None:
    from backend import db as database
    from backend.services.audit import add_log
    from backend.services.detector import detector

    script = Path(ROOT) / "ml" / "train.py"
    status = "error"
    message = "The YOLO training process returned an error."
    try:
        process = subprocess.Popen(
            [sys.executable, str(script)],
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        lines: list[str] = []
        if process.stdout is not None:
            for raw in process.stdout:
                for piece in raw.replace("\r", "\n").splitlines():
                    _note_epoch(piece)
                    if piece.strip():
                        lines.append(piece.rstrip())
                        lines = lines[-30:]
                        with _lock:
                            _state["log"] = "\n".join(lines)
        code = process.wait()
        if code == 0:
            detector.load()
            if detector.mock or detector.model is None or not detector.is_current():
                raise RuntimeError(detector.error or "The trained model could not be loaded for inference.")
            _write_metadata()
            status = "done"
            message = "Training complete. The model is saved and the next scan can use it."
            _log(database, add_log, "INFO", "YOLO training finished and the new model was loaded.")
            with _lock:
                _state["percent"] = 100
                _state["mock"] = bool(detector.mock)
        else:
            status = "error"
            message = lines[-1] if lines else "The YOLO training process returned an error."
            _log(database, add_log, "ERROR", f"YOLO training failed: {message}")
    except Exception as exc:
        status = "error"
        message = str(exc) or "The YOLO training process returned an error."
        _log(database, add_log, "ERROR", f"YOLO training could not be activated: {message}")
    with _lock:
        _state["status"] = status
        _state["message"] = message
        _state["error"] = None if status == "done" else message


def _note_epoch(line: str) -> None:
    match = _EPOCH.search(line)
    if not match:
        return
    epoch = int(match.group(1))
    epochs = int(match.group(2))
    if epochs <= 0 or epoch > epochs or epochs > 1000:
        return
    with _lock:
        _state["epoch"] = epoch
        _state["epochs"] = epochs
        _state["percent"] = int(round(100 * epoch / epochs))
        _state["message"] = f"Training... Epoch {epoch} / {epochs}"


def _write_metadata() -> None:
    path = size_path()
    stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    payload = {
        "name": "SmartEgg egg detector",
        "version": stamp,
        "classes": ["egg"],
        "mapping": {"0": "egg"},
        "size_classes": [item["name"] for item in CLASSES],
        "classification": "per-egg calibrated size bands learned from dataset measurements",
        "dataset": "data/dataset",
        "trained_at": stamp,
        "epochs": _state.get("epochs") or 100,
        "model_path": str(path),
        "metrics": _latest_metrics(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    (path.parent / "metadata.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _latest_metrics() -> dict | None:
    runs = Path(ROOT) / "ml" / "runs"
    if not runs.is_dir():
        return None
    files = [item for item in runs.rglob("metrics.json") if item.is_file()]
    if not files:
        return None
    newest = max(files, key=lambda item: item.stat().st_mtime)
    try:
        return json.loads(newest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _read_metadata() -> dict | None:
    path = size_path().parent / "metadata.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _log(database, add_log, level: str, message: str) -> None:
    if database.SessionLocal is None:
        return
    db = database.SessionLocal()
    try:
        add_log(db, level, message)
        db.commit()
    finally:
        db.close()
