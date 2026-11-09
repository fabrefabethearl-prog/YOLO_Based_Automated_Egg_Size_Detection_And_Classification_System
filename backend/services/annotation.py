"""Egg annotation service for uploaded images.

This module provides automated egg detection, contour annotation, ellipse fitting,
and geometric measurement for phone-captured egg images. It's designed to be
reusable for future ESP32-CAM integration.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
import cv2
import numpy as np

from backend.services.egg_geometry import EggMeasureError, measure_eggs
from backend.services.images import encode_jpeg, to_data_url


@dataclass
class AnnotationResult:
    """Result of egg annotation and measurement."""
    ok: bool
    annotated_image: str  # Base64 data URL
    original_image: str  # Base64 data URL
    measurements: dict
    unit: str
    pixels_per_mm: float | None
    definition: str
    warnings: list[str]
    error: str | None = None


def annotate_uploaded_image(
    image: np.ndarray,
    pixels_per_mm: float | None = None,
) -> AnnotationResult:
    """Annotate an uploaded egg image with contour and ellipse.

    Args:
        image: numpy array of the image (BGR format from OpenCV)
        pixels_per_mm: Optional calibration factor for mm conversion

    Returns:
        AnnotationResult with annotated image, measurements, and metadata

    Raises:
        EggMeasureError: If egg detection fails
    """
    if image is None or image.ndim != 3 or image.shape[0] < 20 or image.shape[1] < 20:
        return AnnotationResult(
            ok=False,
            annotated_image="",
            original_image="",
            measurements={},
            unit="px",
            pixels_per_mm=None,
            definition="",
            warnings=[],
            error="Image is too small or invalid.",
        )

    # Use existing egg geometry service for detection and measurement
    try:
        result = measure_eggs(image, pixels_per_mm)
    except EggMeasureError as exc:
        return AnnotationResult(
            ok=False,
            annotated_image="",
            original_image=to_data_url(encode_jpeg(image)),
            measurements={},
            unit="px",
            pixels_per_mm=None,
            definition="",
            warnings=[],
            error=exc.message,
        )

    if not result.get("ok"):
        return AnnotationResult(
            ok=False,
            annotated_image="",
            original_image=to_data_url(encode_jpeg(image)),
            measurements={},
            unit="px",
            pixels_per_mm=None,
            definition="",
            warnings=[],
            error="Could not detect egg contour.",
        )

    # Create annotated image with contour and ellipse
    annotated = image.copy()
    eggs = result.get("eggs", [])
    if not eggs:
        return AnnotationResult(
            ok=False,
            annotated_image="",
            original_image=to_data_url(encode_jpeg(image)),
            measurements={},
            unit="px",
            pixels_per_mm=None,
            definition="",
            warnings=[],
            error="No egg detected in the image.",
        )

    egg = eggs[0]  # Use the highest-confidence egg
    contour_points = egg.get("contour", [])
    ellipse = egg.get("ellipse", {})

    # Draw contour in green
    if contour_points and len(contour_points) >= 2:
        contour_array = np.array(contour_points, dtype=np.int32).reshape((-1, 1, 2))
        cv2.polylines(annotated, [contour_array], True, (0, 255, 0), 2)

    # Draw ellipse in blue
    if ellipse:
        center_x = ellipse.get("center_x", 0)
        center_y = ellipse.get("center_y", 0)
        major = ellipse.get("major_px", 0)
        minor = ellipse.get("minor_px", 0)
        angle = ellipse.get("angle_deg", 0)

        center = (int(center_x), int(center_y))
        axes = (int(major / 2), int(minor / 2))
        cv2.ellipse(annotated, center, axes, angle, 0, 360, (255, 0, 0), 2)

        # Draw center point in red
        cv2.circle(annotated, center, 4, (0, 0, 255), -1)

        # Draw major and minor axes
        angle_rad = math.radians(angle)
        cos_a = math.cos(angle_rad)
        sin_a = math.sin(angle_rad)

        # Major axis line
        major_end_x = center_x + (major / 2) * cos_a
        major_end_y = center_y + (major / 2) * sin_a
        major_start_x = center_x - (major / 2) * cos_a
        major_start_y = center_y - (major / 2) * sin_a
        cv2.line(
            annotated,
            (int(major_start_x), int(major_start_y)),
            (int(major_end_x), int(major_end_y)),
            (255, 0, 0),
            1,
        )

        # Minor axis line
        minor_end_x = center_x - (minor / 2) * sin_a
        minor_end_y = center_y + (minor / 2) * cos_a
        minor_start_x = center_x + (minor / 2) * sin_a
        minor_start_y = center_y - (minor / 2) * cos_a
        cv2.line(
            annotated,
            (int(minor_start_x), int(minor_start_y)),
            (int(minor_end_x), int(minor_end_y)),
            (255, 0, 0),
            1,
        )

    return AnnotationResult(
        ok=True,
        annotated_image=to_data_url(encode_jpeg(annotated)),
        original_image=to_data_url(encode_jpeg(image)),
        measurements=egg,
        unit=result.get("unit", "px"),
        pixels_per_mm=result.get("pixels_per_mm"),
        definition=result.get("definition", ""),
        warnings=result.get("warnings", []),
        error=None,
    )


def format_annotation_response(result: AnnotationResult) -> dict:
    """Format AnnotationResult as API response."""
    response = {
        "ok": result.ok,
        "annotated_image": result.annotated_image,
        "original_image": result.original_image,
        "measurements": result.measurements,
        "unit": result.unit,
        "pixels_per_mm": result.pixels_per_mm,
        "definition": result.definition,
        "warnings": result.warnings,
    }
    if result.error:
        response["error"] = result.error
    return response
