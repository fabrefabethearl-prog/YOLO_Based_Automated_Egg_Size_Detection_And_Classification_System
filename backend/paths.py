"""Filesystem locations for the EggScan project."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
SIZE_DIR = ROOT / "size"
ML_RUNS = ROOT / "ml" / "runs"
DATA_YAML = ROOT / "ml" / "data.yaml"

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
# Photo extensions. A matching name that cannot be decoded is reported as corrupt.
# Any other file is still kept when its bytes decode as an image.
IMAGE_LIKE_SUFFIXES = IMAGE_SUFFIXES | {
    ".jpe",
    ".jfif",
    ".tif",
    ".tiff",
    ".gif",
    ".jp2",
    ".j2k",
    ".jpx",
    ".heic",
    ".heif",
    ".avif",
    ".ico",
    ".tga",
    ".ppm",
    ".pgm",
    ".pbm",
    ".pnm",
    ".dib",
    ".dds",
    ".exr",
    ".hdr",
    ".pic",
    ".dng",
}


def data_dir() -> Path:
    override = os.getenv("EGGSCAN_DATA_DIR")
    if override:
        return Path(override)
    return ROOT / "data"


def scans_dir() -> Path:
    return data_dir() / "scans"


def dataset_dir() -> Path:
    return data_dir() / "dataset"


def size_path() -> Path:
    override = os.getenv("EGGSCAN_SIZE")
    if override:
        return Path(override)
    return SIZE_DIR / "best.pt"


def default_db_url() -> str:
    path = (data_dir() / "eggscan.db").resolve()
    return "sqlite:///" + path.as_posix()


def ensure_dirs() -> None:
    scans_dir().mkdir(parents=True, exist_ok=True)
    for split in ("train", "val", "test"):
        (dataset_dir() / "images" / split).mkdir(parents=True, exist_ok=True)
        (dataset_dir() / "labels" / split).mkdir(parents=True, exist_ok=True)
    SIZE_DIR.mkdir(parents=True, exist_ok=True)
    ML_RUNS.mkdir(parents=True, exist_ok=True)
