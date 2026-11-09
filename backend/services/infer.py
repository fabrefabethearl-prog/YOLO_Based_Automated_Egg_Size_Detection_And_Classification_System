"""Single-flight inference. Frames wider than 640px are reduced before the model runs."""

from __future__ import annotations

import threading

import cv2
import numpy as np

from backend.services.detector import Detection, EggHit, detector

_LOCK = threading.Lock()
MAX_WIDTH = 640


def downscale(image: np.ndarray, max_width: int = MAX_WIDTH) -> tuple[np.ndarray, float]:
    """Return a frame at most max_width wide, and the factor to map boxes back."""
    height, width = image.shape[:2]
    if width <= max_width:
        return image, 1.0
    factor = max_width / float(width)
    resized = cv2.resize(
        image,
        (max_width, max(1, int(round(height * factor)))),
        interpolation=cv2.INTER_AREA,
    )
    return resized, 1.0 / factor


def _scale_box(bbox: tuple[int, int, int, int] | None, factor: float, width: int, height: int):
    if bbox is None or factor == 1.0:
        return bbox
    x, y, box_w, box_h = bbox
    left = int(round(x * factor))
    top = int(round(y * factor))
    scaled_w = int(round(box_w * factor))
    scaled_h = int(round(box_h * factor))
    left = max(0, min(left, width - 1))
    top = max(0, min(top, height - 1))
    scaled_w = max(0, min(scaled_w, width - left))
    scaled_h = max(0, min(scaled_h, height - top))
    if scaled_w < 2 or scaled_h < 2:
        return None
    return left, top, scaled_w, scaled_h


def detect_scaled(image: np.ndarray, threshold: float, *, wait: bool) -> Detection | None:
    """Run the loaded model. Preview calls wait=False so a busy detector returns None."""
    acquired = _LOCK.acquire(blocking=wait)
    if not acquired:
        return None
    try:
        height, width = image.shape[:2]
        small, factor = downscale(image)
        outcome = detector.detect(small, threshold)
        outcome.bbox = _scale_box(outcome.bbox, factor, width, height)
        return outcome
    finally:
        _LOCK.release()


def detect_frame(image: np.ndarray, threshold: float, *, wait: bool) -> tuple[list[EggHit], EggHit | None, float, str | None] | None:
    """Every egg in the frame, with boxes mapped back to the original pixels."""
    acquired = _LOCK.acquire(blocking=wait)
    if not acquired:
        return None
    try:
        height, width = image.shape[:2]
        small, factor = downscale(image)
        hits, best, elapsed, error = detector.collect(small, threshold)
        if error:
            return [], None, elapsed, error
        scaled: list[EggHit] = []
        for hit in hits:
            box = _scale_box(hit.bbox, factor, width, height)
            if box is None:
                continue
            scaled.append(EggHit(hit.confidence, box, hit.class_name, hit.class_id))
        best_box = _scale_box(best.bbox, factor, width, height) if best is not None else None
        best_scaled = None if best is None or best_box is None else EggHit(best.confidence, best_box, best.class_name, best.class_id)
        return scaled, best_scaled, elapsed, None
    finally:
        _LOCK.release()
