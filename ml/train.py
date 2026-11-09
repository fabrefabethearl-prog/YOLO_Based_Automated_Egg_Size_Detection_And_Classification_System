"""Train an egg detector and activate its best checkpoint.

The class-specific annotations remain available for the calibrated size bands.

Run from the project root:

    python ml/train.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def data_yaml_source() -> Path:
    generated = ROOT / "data" / "dataset" / "data.yaml"
    if generated.is_file():
        return generated
    return ROOT / "ml" / "data.yaml"


def resolved_data_yaml() -> Path:
    dataset = (ROOT / "data" / "dataset").resolve()
    source = data_yaml_source().read_text(encoding="utf-8")
    lines = []
    replaced = False
    for line in source.splitlines():
        if line.startswith("path:"):
            lines.append("path: " + dataset.as_posix())
            replaced = True
        else:
            lines.append(line)
    if not replaced:
        lines.insert(0, "path: " + dataset.as_posix())
    destination = ROOT / "ml" / "runs" / "data.resolved.yaml"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return destination


def count_images(split: str) -> int:
    folder = ROOT / "data" / "dataset" / "images" / split
    if not folder.is_dir():
        return 0
    return sum(1 for path in folder.iterdir() if path.suffix.lower() in IMAGE_SUFFIXES)


def main() -> int:
    train_count = count_images("train")
    val_count = count_images("val")
    if train_count == 0 or val_count == 0:
        print("Need images in data/dataset/images/train and data/dataset/images/val.")
        print("See ml/README.md for collecting, labelling, and the 70/20/10 split.")
        return 1

    print("Training one egg detection class; each egg size is assigned from dataset measurements.")

    from ultralytics import YOLO

    model = YOLO("yolov8n.pt")
    metrics = model.train(
        data=str(resolved_data_yaml()),
        imgsz=640,
        epochs=100,
        patience=20,
        single_cls=True,
        project=str(ROOT / "ml" / "runs"),
        name="egg-object",
        exist_ok=False,
    )
    save_dir = Path(model.trainer.save_dir)
    best = save_dir / "weights" / "best.pt"
    if not best.is_file():
        print(f"Training finished but {best} was not created.")
        return 1

    data_yaml = resolved_data_yaml()
    test_count = count_images("test")
    evaluation_split = "test" if test_count else "val"
    evaluation_count = test_count if test_count else val_count
    candidate = YOLO(str(best))
    names = getattr(candidate, "names", {}) or {}
    if len(names) != 1:
        print(f"Expected one egg detector class, found {len(names)}. The active model was not changed.")
        return 1
    evaluation = candidate.val(
        data=str(data_yaml),
        split=evaluation_split,
        imgsz=640,
        single_cls=True,
        verbose=False,
    )
    # Run an actual prediction against a held-out image before making this checkpoint active.
    validation_images = sorted(
        path for path in (ROOT / "data" / "dataset" / "images" / evaluation_split).iterdir()
        if path.suffix.lower() in IMAGE_SUFFIXES
    )
    if not validation_images:
        print(f"No {evaluation_split} images are available for model verification.")
        return 1
    predictions = candidate.predict(source=str(validation_images[0]), imgsz=640, verbose=False)
    if not predictions:
        print("The trained checkpoint did not return an inference result; the active model was not changed.")
        return 1

    results = getattr(evaluation, "results_dict", None) or {}
    map50 = _metric(results, "mAP50(B)")
    map5095 = _metric(results, "mAP50-95(B)")
    destination = ROOT / "size" / "best.pt"
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = destination.with_name(f".best-{os.getpid()}.staged.pt")
    try:
        shutil.copy2(best, staged)
        # Load and infer from the exact staged bytes before touching the active checkpoint.
        staged_model = YOLO(str(staged))
        staged_predictions = staged_model.predict(source=str(validation_images[0]), imgsz=640, verbose=False)
        if not staged_predictions:
            raise RuntimeError("The staged checkpoint returned no inference result.")
        if destination.is_file():
            backup_dir = destination.parent / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup = backup_dir / f"best-{stamp}.pt"
            shutil.copy2(destination, backup)
            print(f"Backed up the previous active model to {backup}")
        os.replace(staged, destination)
    except Exception as exc:
        staged.unlink(missing_ok=True)
        print(f"Model activation failed; the previous active checkpoint is unchanged: {exc}")
        return 1
    print(f"Evaluation split: {evaluation_split} ({evaluation_count} images)")
    print(f"mAP50: {map50}")
    print(f"mAP50-95: {map5095}")
    metrics_path = save_dir / "metrics.json"
    metrics_path.write_text(
        json.dumps(
            {
                "map50": None if map50 == "n/a" else map50,
                "map50_95": None if map5095 == "n/a" else map5095,
                "epochs": 100,
                "date": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "source": save_dir.name,
                "evaluation_split": evaluation_split,
                "evaluation_images": evaluation_count,
            }
        ),
        encoding="utf-8",
    )
    stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    size_metadata = {
        "name": "SmartEgg egg detector",
        "version": stamp,
        "classes": ["egg"],
        "mapping": {"0": "egg"},
        "size_classes": ["Small", "Medium", "Large", "Extra Large", "Jumbo"],
        "classification": "per-egg calibrated size bands learned from dataset measurements",
        "dataset": "data/dataset",
        "trained_at": stamp,
        "epochs": 100,
        "model_path": str(destination),
        "metrics": {
            "map50": None if map50 == "n/a" else map50,
            "map50_95": None if map5095 == "n/a" else map5095,
            "epochs": 100,
            "date": stamp,
            "source": save_dir.name,
            "evaluation_split": evaluation_split,
            "evaluation_images": evaluation_count,
        },
    }
    (destination.parent / "metadata.json").write_text(json.dumps(size_metadata, indent=2), encoding="utf-8")
    print(f"Wrote {metrics_path}")
    print(f"Copied the trained model to {destination}")
    print("Restart the server so EggScan loads the new file and leaves mock mode.")
    return 0


def _metric(results: dict, needle: str):
    for key, value in results.items():
        if needle in key:
            return value
    return "n/a"


if __name__ == "__main__":
    sys.exit(main())
