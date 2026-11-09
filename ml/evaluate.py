"""Evaluate each per-egg size class on test, or validation when test is empty.

Run from the project root:

    python ml/evaluate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def resolved_data_yaml() -> Path:
    dataset = (ROOT / "data" / "dataset").resolve()
    source = (ROOT / "ml" / "data.yaml").read_text(encoding="utf-8")
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


def main() -> int:
    model_file = ROOT / "size" / "best.pt"
    if not model_file.is_file():
        print("Missing size/best.pt. Train first with python ml/train.py, or copy your checkpoint there.")
        return 1

    test_dir = ROOT / "data" / "dataset" / "images" / "test"
    val_dir = ROOT / "data" / "dataset" / "images" / "val"
    test_images = [path for path in test_dir.glob("*") if path.suffix.lower() in IMAGE_SUFFIXES] if test_dir.is_dir() else []
    val_images = [path for path in val_dir.glob("*") if path.suffix.lower() in IMAGE_SUFFIXES] if val_dir.is_dir() else []
    split = "test" if test_images else "val"
    if not test_images and val_images:
        print("Test split is empty; evaluating the held-out validation split instead.")
    if not test_images and not val_images:
        print("No images in data/dataset/images/test or val.")
        return 1

    from ultralytics import YOLO

    model = YOLO(str(model_file))
    raw_names = getattr(model, "names", {}) or {}
    found = tuple(str(raw_names[index]) for index in range(len(raw_names))) if isinstance(raw_names, dict) else tuple(map(str, raw_names))
    if len(found) != 1:
        print(f"Expected one egg detection class; found {found}. Retrain with python ml/train.py.")
        return 1

    metrics = model.val(
        data=str(resolved_data_yaml()),
        split=split,
        imgsz=640,
        single_cls=True,
        project=str(ROOT / "ml" / "runs"),
        name="egg-validation",
        exist_ok=True,
    )
    box = metrics.box
    print(f"Split: {split} ({len(test_images) if split == 'test' else len(val_images)} images)")
    print(f"Detector class: egg (checkpoint label: {found[0]})")
    print(f"Precision: {box.mp:.4f}")
    print(f"Recall: {box.mr:.4f}")
    print(f"mAP50: {box.map50:.4f}")
    print(f"mAP50-95: {box.map:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
