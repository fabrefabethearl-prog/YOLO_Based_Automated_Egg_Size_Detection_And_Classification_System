"""Fill the database with presentation rows. Not used on normal startup.

Run from the project root:

    python -m backend.seed_demo
    python -m backend.seed_demo --force
"""

from __future__ import annotations

import argparse
import random
from datetime import timedelta
from pathlib import Path

import cv2
import numpy as np

from backend import db as database
from backend.db import init_db
from backend.models import BenchmarkTrial, Log, Scan, utcnow
from backend.paths import scans_dir
from backend.services.images import encode_jpeg
from backend.services.settings_store import get_categories
from backend.services.size import area_mm2, class_for_area

TEMPLATES = [
    ("Small", 52, 40),
    ("Medium", 58, 43),
    ("Large", 60, 44),
    ("Extra Large", 62, 45),
    ("Jumbo", 64, 47),
]


def _egg_image(title: str) -> bytes:
    canvas = np.full((360, 480, 3), (30, 55, 44), dtype=np.uint8)
    cv2.ellipse(canvas, (240, 180), (70, 100), 0, 0, 360, (230, 236, 228), -1)
    cv2.putText(canvas, title, (150, 330), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (217, 154, 43), 2, cv2.LINE_AA)
    return encode_jpeg(canvas)


def seed(force: bool) -> None:
    init_db()
    db = database.SessionLocal()
    try:
        existing = db.query(Scan).count()
        if existing and not force:
            print(f"Database already has {existing} scans. Re-run with --force to replace demo rows.")
            return
        if force:
            for scan in db.query(Scan).all():
                path = scans_dir() / Path(scan.image_path).name
                if path.is_file():
                    path.unlink()
            db.query(Scan).delete()
            db.query(BenchmarkTrial).delete()
            db.query(Log).delete()
            db.commit()

        categories = get_categories(db)
        rng = random.Random(321)
        now = utcnow()
        for index in range(28):
            _name, length, width = TEMPLATES[index % len(TEMPLATES)]
            length += rng.uniform(-0.6, 0.6)
            width += rng.uniform(-0.4, 0.4)
            length = round(length, 2)
            width = round(width, 2)
            area = round(area_mm2(length, width), 2)
            created = now - timedelta(days=rng.randint(0, 13), hours=rng.randint(0, 8), minutes=rng.randint(0, 59))
            predicted = class_for_area(area, categories)
            labelled = rng.random() > 0.2
            filename = f"demo_{index:02d}.jpg"
            (scans_dir() / filename).write_bytes(_egg_image(predicted))
            db.add(
                Scan(
                    created_at=created,
                    image_path=filename,
                    length_mm=length,
                    width_mm=width,
                    area_mm2=area,
                    predicted_class=predicted,
                    actual_class=_name if labelled else None,
                    confidence=round(rng.uniform(0.78, 0.97), 4),
                    inference_ms=round(rng.uniform(18, 55), 2),
                    is_mock=True,
                )
            )
        for trial_no, eggs, manual_s, scan_s, manual_ok, scan_ok in (
            (1, 30, 186, 74, 26, 29),
            (2, 30, 171, 69, 27, 28),
            (3, 40, 240, 96, 34, 38),
        ):
            db.add(
                BenchmarkTrial(
                    trial_no=trial_no,
                    eggs=eggs,
                    manual_time_s=manual_s,
                    eggscan_time_s=scan_s,
                    manual_correct=manual_ok,
                    eggscan_correct=scan_ok,
                    created_at=now - timedelta(days=trial_no),
                )
            )
        db.add(Log(level="INFO", message="Demo rows loaded for a presentation.", created_at=now))
        db.commit()
        print("Loaded 28 demo scans and 3 benchmark trials.")
        print("These rows are marked mock. Delete them from Scan records when you start real trials.")
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Load EggScan presentation data.")
    parser.add_argument("--force", action="store_true", help="Replace existing scans, trials, and logs.")
    args = parser.parse_args()
    seed(args.force)


if __name__ == "__main__":
    main()
