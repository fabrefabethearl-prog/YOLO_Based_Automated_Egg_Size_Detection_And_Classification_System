"""Decode uploads and encode annotated JPEGs."""

from __future__ import annotations

import base64
import io

import cv2
import numpy as np

MAX_BYTES = 10 * 1024 * 1024
MAX_EDGE = 8000


class ImageError(ValueError):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def decode_image(raw: bytes) -> np.ndarray:
    if not raw:
        raise ImageError(400, "The image file is empty.")
    if len(raw) > MAX_BYTES:
        raise ImageError(413, "Image must be 10 MB or smaller.")
    array = np.frombuffer(raw, dtype=np.uint8)
    image = cv2.imdecode(array, cv2.IMREAD_COLOR)
    if image is None:
        image = _decode_with_pillow(raw)
    if image is None:
        raise ImageError(400, "Could not read that image.")
    height, width = image.shape[:2]
    if max(height, width) > MAX_EDGE:
        raise ImageError(400, "Image dimensions are too large.")
    return image


def decode_base64_image(value: str) -> bytes:
    if not isinstance(value, str) or not value.strip():
        raise ImageError(400, "Attach an image file or a base64 frame.")
    payload = value.strip()
    if payload.startswith("data:") and "," in payload:
        payload = payload.split(",", 1)[1]
    try:
        return base64.b64decode(payload, validate=False)
    except Exception as exc:
        raise ImageError(400, "Could not read that image.") from exc


def _decode_with_pillow(raw: bytes) -> np.ndarray | None:
    """Read formats OpenCV skips, such as some TIFF, GIF, and icon files."""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(io.BytesIO(raw)) as handle:
            rgb = np.array(handle.convert("RGB"))
    except Exception:
        return None
    if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.size == 0:
        return None
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def encode_jpeg(image: np.ndarray) -> bytes:
    ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        raise ImageError(500, "Could not save the annotated image.")
    return buffer.tobytes()


def to_data_url(jpeg_bytes: bytes) -> str:
    encoded = base64.b64encode(jpeg_bytes).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"
