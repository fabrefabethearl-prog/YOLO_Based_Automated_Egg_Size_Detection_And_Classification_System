"""YOLO egg detector, with an OpenCV contour fallback when the model file is missing."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import cv2
import numpy as np

from backend.paths import size_path


@dataclass
class Detection:
    found: bool
    confidence: float
    inference_ms: float
    bbox: tuple[int, int, int, int] | None
    message: str
    error: str | None = None
    class_name: str | None = None
    egg_count: int = 0


@dataclass
class EggHit:
    """One egg in a frame. bbox is x, y, width, height in pixels."""

    confidence: float
    bbox: tuple[int, int, int, int]
    class_name: str | None = None
    class_id: int | None = None


class DetectorService:
    def __init__(self) -> None:
        self.model = None
        self.mock = True
        self.error: str | None = None
        self.loaded_path: str | None = None
        self.loaded_signature: tuple[int, int] | None = None

    def load(self) -> None:
        self.error = None
        if os.getenv("EGGSCAN_FORCE_MOCK") == "1":
            self.model = None
            self.mock = True
            self.loaded_path = None
            self.loaded_signature = None
            return
        path = size_path()
        if not path.is_file() or path.stat().st_size < 1000:
            self.model = None
            self.mock = True
            return
        try:
            from ultralytics import YOLO

            # Construct the replacement first; live scans keep their current model until
            # the new checkpoint has loaded successfully.
            model = YOLO(str(path))
        except Exception as exc:
            if self.model is None:
                self.mock = True
            self.error = str(exc)
            return
        stat = path.stat()
        self.model = model
        self.mock = False
        self.loaded_path = str(path.resolve())
        self.loaded_signature = (stat.st_size, stat.st_mtime_ns)

    def is_current(self) -> bool:
        path = size_path()
        if self.mock or self.model is None or not path.is_file() or not self.loaded_signature:
            return False
        try:
            stat = path.stat()
            return self.loaded_path == str(path.resolve()) and self.loaded_signature == (stat.st_size, stat.st_mtime_ns)
        except OSError:
            return False

    def detect(self, image: np.ndarray, threshold: float) -> Detection:
        """Highest-confidence egg. Saved scans still store that one egg."""
        started = time.perf_counter()
        try:
            hits, best = self._candidates(image, threshold)
        except Exception as exc:
            elapsed = (time.perf_counter() - started) * 1000
            return Detection(False, 0.0, elapsed, None, "The model could not read this frame.", error=str(exc))
        elapsed = (time.perf_counter() - started) * 1000
        return _as_detection(hits, best, elapsed, threshold)

    def collect(self, image: np.ndarray, threshold: float) -> tuple[list[EggHit], EggHit | None, float, str | None]:
        """Every egg at or above the threshold, plus the strongest candidate and elapsed ms."""
        started = time.perf_counter()
        try:
            hits, best = self._candidates(image, threshold)
        except Exception as exc:
            elapsed = (time.perf_counter() - started) * 1000
            return [], None, elapsed, str(exc)
        elapsed = (time.perf_counter() - started) * 1000
        return hits, best, elapsed, None

    def _candidates(self, image: np.ndarray, threshold: float) -> tuple[list[EggHit], EggHit | None]:
        if self.mock or self.model is None:
            found = _mock_hits(image)
        else:
            found = _yolo_hits(self.model, image, threshold)
        best = max(found, key=lambda hit: hit.confidence) if found else None
        kept = [hit for hit in found if hit.confidence >= threshold]
        return kept, best


detector = DetectorService()


def annotate(image: np.ndarray, bbox: tuple[int, int, int, int], label: str) -> np.ndarray:
    canvas = image.copy()
    x, y, width, height = bbox
    color = (43, 154, 217)
    cv2.rectangle(canvas, (x, y), (x + width, y + height), color, 2)
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.55
    thickness = 1
    (text_width, text_height), baseline = cv2.getTextSize(label, font, scale, thickness)
    top = max(0, y - text_height - baseline - 8)
    cv2.rectangle(canvas, (x, top), (x + text_width + 10, top + text_height + baseline + 8), color, -1)
    cv2.putText(canvas, label, (x + 5, top + text_height + 4), font, scale, (19, 55, 44), thickness, cv2.LINE_AA)
    return canvas


def _clamp_box(x1: float, y1: float, x2: float, y2: float, width: int, height: int) -> tuple[int, int, int, int] | None:
    left = max(0, min(int(round(x1)), width - 1))
    top = max(0, min(int(round(y1)), height - 1))
    right = max(0, min(int(round(x2)), width))
    bottom = max(0, min(int(round(y2)), height))
    box_w = right - left
    box_h = bottom - top
    if box_w < 2 or box_h < 2:
        return None
    return left, top, box_w, box_h


def _as_detection(hits: list[EggHit], best: EggHit | None, elapsed: float, threshold: float) -> Detection:
    if not hits:
        if best is not None and 0 < best.confidence < threshold:
            return Detection(
                False,
                best.confidence,
                elapsed,
                best.bbox,
                "Confidence is below the threshold, so this frame was not saved.",
                class_name=best.class_name,
            )
        return Detection(False, 0.0, elapsed, None, "No egg detected.")
    top = max(hits, key=lambda hit: hit.confidence)
    return Detection(
        True,
        top.confidence,
        elapsed,
        top.bbox,
        "Egg detected.",
        class_name=top.class_name,
        egg_count=len(hits),
    )


def _yolo_hits(model, image: np.ndarray, threshold: float) -> list[EggHit]:
    results = model.predict(source=image, conf=threshold, verbose=False, imgsz=640)
    boxes = results[0].boxes if results else None
    if boxes is None or len(boxes) == 0:
        return []
    height, width = image.shape[:2]
    names = getattr(model, "names", {}) or {}
    confidences = boxes.conf.detach().cpu().numpy()
    classes = boxes.cls.detach().cpu().numpy()
    xyxy = boxes.xyxy.detach().cpu().numpy()
    hits: list[EggHit] = []
    for index, confidence in enumerate(confidences):
        bbox = _clamp_box(float(xyxy[index][0]), float(xyxy[index][1]), float(xyxy[index][2]), float(xyxy[index][3]), width, height)
        if bbox is None:
            continue
        class_id = int(classes[index])
        class_name = names.get(class_id) if isinstance(names, dict) else names[class_id]
        hits.append(
            EggHit(
                float(confidence),
                bbox,
                str(class_name) if class_name else None,
                class_id,
            )
        )
    return hits


def egg_box(image: np.ndarray, *, max_area_fraction: float = 0.85) -> tuple[int, int, int, int] | None:
    """Egg box from the same contour detector Live scan uses."""
    found = _best_egg_contour(image, max_area_fraction=max_area_fraction)
    if found is None:
        return None
    return found[1]


def _mock_hits(image: np.ndarray) -> list[EggHit]:
    return [EggHit(score, box) for score, box in _egg_contours(image)]


def _best_egg_contour(
    image: np.ndarray,
    max_area_fraction: float = 0.85,
) -> tuple[float, tuple[int, int, int, int]] | None:
    found = _egg_contours(image, max_area_fraction=max_area_fraction)
    if not found:
        return None
    return max(found, key=lambda item: item[0])


def _box_iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    left = max(ax, bx)
    top = max(ay, by)
    right = min(ax + aw, bx + bw)
    bottom = min(ay + ah, by + bh)
    overlap = max(0, right - left) * max(0, bottom - top)
    union = aw * ah + bw * bh - overlap
    if union <= 0:
        return 0.0
    return overlap / union


def _keep_separate(found: list[tuple[float, tuple[int, int, int, int]]]) -> list[tuple[float, tuple[int, int, int, int]]]:
    """Drop a second contour that sits on the same egg."""
    ordered = sorted(found, key=lambda item: item[0], reverse=True)
    kept: list[tuple[float, tuple[int, int, int, int]]] = []
    for score, box in ordered:
        if any(_box_iou(box, other) > 0.45 for _other_score, other in kept):
            continue
        kept.append((score, box))
    return kept


def _egg_contours(
    image: np.ndarray,
    max_area_fraction: float = 0.85,
) -> list[tuple[float, tuple[int, int, int, int]]]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    height, width = gray.shape
    image_area = float(height * width)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    found: list[tuple[float, tuple[int, int, int, int]]] = []

    for invert in (False, True):
        flag = cv2.THRESH_BINARY_INV if invert else cv2.THRESH_BINARY
        _, mask = cv2.threshold(blur, 0, 255, flag + cv2.THRESH_OTSU)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            area = float(cv2.contourArea(contour))
            if area < image_area * 0.02 or area > image_area * max_area_fraction:
                continue
            x, y, box_w, box_h = cv2.boundingRect(contour)
            short = max(1, min(box_w, box_h))
            aspect = max(box_w, box_h) / short
            if aspect > 2.4:
                continue
            if len(contour) < 5:
                continue
            hull_area = float(cv2.contourArea(cv2.convexHull(contour))) or 1.0
            solidity = area / hull_area
            if solidity < 0.8:
                continue
            aspect_penalty = min(abs(aspect - 1.4) / 1.4, 1.0)
            score = 0.95 * solidity * (1 - 0.25 * aspect_penalty)
            found.append((float(score), (x, y, box_w, box_h)))

    return _keep_separate(found)
