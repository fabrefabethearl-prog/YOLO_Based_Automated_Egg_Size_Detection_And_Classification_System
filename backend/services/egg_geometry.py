"""Measure an egg from a phone photo. The original image is never modified.

Length is the major-axis diameter of the fitted ellipse. Width is the minor-axis
diameter, perpendicular to that axis. Both stay in pixels unless a pixels-per-mm
scale is supplied. The contour and the ellipse are kept separate: the ellipse is
only an approximation of the shell.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

DEFINITION = (
    "Egg length and width are the contour projections along and across the fitted ellipse's major axis. "
    "Ellipse axes are reported separately as an approximation. "
    "Orientation is measured clockwise from the image's positive horizontal axis and normalized to [-90, 90) degrees."
)
_SECTIONS = (0.2, 0.35, 0.5, 0.65, 0.8)
_MIN_COMPONENT_AREA_RATIO = 0.001


class EggMeasureError(ValueError):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def eggs_from_boxes(
    shape: tuple[int, ...],
    boxes: list[tuple[tuple[int, int, int, int], float]],
    pixels_per_mm: float | None = None,
) -> dict:
    """Turn detector boxes into ovals. Each box is one egg, even when shells touch."""
    if len(shape) < 2 or shape[0] < 20 or shape[1] < 20:
        raise EggMeasureError("That image is too small to measure an egg.")
    height, width = int(shape[0]), int(shape[1])
    scale = float(pixels_per_mm) if pixels_per_mm and pixels_per_mm > 0 else None
    eggs = []
    for bbox, score in boxes:
        published = _egg_from_box(bbox, float(score), width, height, scale)
        if published is not None:
            eggs.append(published)
    if not eggs:
        raise EggMeasureError(
            "The egg could not be separated from the background. Use a darker background and keep the whole egg in frame, or draw the oval yourself."
        )
    warnings = []
    if scale is None:
        warnings.append("Measurements are in pixels. Set pixels per mm in Settings to convert them to millimetres.")
    return {
        "ok": True,
        "unit": "px",
        "pixels_per_mm": None if scale is None else round(scale, 4),
        "definition": DEFINITION,
        "warnings": warnings,
        "eggs": eggs[:8],
    }


def _egg_from_box(
    bbox: tuple[int, int, int, int],
    score: float,
    width: int,
    height: int,
    scale: float | None,
) -> dict | None:
    x, y, box_w, box_h = bbox
    if box_w < 2 or box_h < 2:
        return None
    x = max(0, min(int(x), width - 1))
    y = max(0, min(int(y), height - 1))
    box_w = max(2, min(int(box_w), width - x))
    box_h = max(2, min(int(box_h), height - y))
    center_x = x + box_w / 2
    center_y = y + box_h / 2
    major = float(max(box_w, box_h))
    minor = float(min(box_w, box_h))
    angle = 90.0 if box_h > box_w else 0.0
    contour = _ellipse_points(center_x, center_y, major, minor, angle)
    area = math.pi * (major / 2) * (minor / 2)
    perimeter = math.pi * (3 * (major + minor) / 2 - math.sqrt(max((3 * major + minor) * (major + 3 * minor), 0) / 4))
    length_mm, width_mm, has_millimetres = _millimetres(major, minor, scale)
    sections = []
    for position in _SECTIONS:
        along = 2 * position - 1
        span = minor * math.sqrt(max(0.0, 1 - along * along))
        if span < 2:
            continue
        width_value = None if scale is None else round(span / scale, 2)
        sections.append({"position": position, "width_px": round(span, 2), "width_mm": width_value})
    return {
        "score": round(max(0.0, min(score, 1.0)), 3),
        "contour": contour,
        "ellipse": {
            "center_x": round(center_x, 2),
            "center_y": round(center_y, 2),
            "major_px": round(major, 2),
            "minor_px": round(minor, 2),
            "angle_deg": round(angle, 2),
        },
        "bbox": {"x": x, "y": y, "width": box_w, "height": box_h},
        "area_px": round(area, 1),
        "perimeter_px": round(perimeter, 1),
        "length_px": round(major, 2),
        "width_px": round(minor, 2),
        "length_mm": length_mm,
        "width_mm": width_mm,
        "millimetres": has_millimetres,
        "cross_sections": sections,
        "definition": DEFINITION,
    }


def _ellipse_points(center_x: float, center_y: float, major: float, minor: float, angle_deg: float) -> list[list[float]]:
    angle = math.radians(angle_deg)
    points = []
    for step in range(36):
        theta = 2 * math.pi * step / 36
        local_x = (major / 2) * math.cos(theta)
        local_y = (minor / 2) * math.sin(theta)
        x = center_x + local_x * math.cos(angle) - local_y * math.sin(angle)
        y = center_y + local_x * math.sin(angle) + local_y * math.cos(angle)
        points.append([round(x, 1), round(y, 1)])
    return points


def measure_eggs(image: np.ndarray, pixels_per_mm: float | None = None) -> dict:
    """Detect egg contours using image contrast and return geometry and sizes."""
    if image is None or image.ndim != 3 or image.shape[0] < 20 or image.shape[1] < 20:
        raise EggMeasureError("That image is too small to measure an egg.")
    working = image.copy()
    gray = cv2.cvtColor(working, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    height, width = gray.shape
    clipped = False
    found: list[dict] = []
    for mask in _masks(blur, working):
        split_candidates, split_components = _split_components(mask, width, height)
        for candidate in _contours(mask, width, height):
            if candidate.get("clipped"):
                clipped = True
                continue
            if any(_iou(candidate["bbox"], component) > 0.75 for component in split_components):
                continue
            found.append(candidate)
        found.extend(split_candidates)
    found = _separate(found)
    if not found:
        if clipped:
            raise EggMeasureError(
                "The egg is cut off by the edge of the photo. Keep the whole egg in frame, or draw the oval yourself."
            )
        raise EggMeasureError(
            "The egg could not be separated from the background. Use a darker background and keep the whole egg in frame, or draw the oval yourself."
        )
    scale = float(pixels_per_mm) if pixels_per_mm and pixels_per_mm > 0 else None
    eggs = [_publish(item, scale) for item in found[:8]]
    warnings = []
    if scale is None:
        warnings.append("Measurements are in pixels. Set pixels per mm in Settings to convert them to millimetres.")
    return {
        "ok": True,
        "unit": "px",
        "pixels_per_mm": None if scale is None else round(scale, 4),
        "definition": DEFINITION,
        "warnings": warnings,
        "eggs": eggs,
    }


def measure_egg_at_point(
    image: np.ndarray,
    x: float,
    y: float,
    pixels_per_mm: float | None = None,
) -> dict:
    """Measure the egg under an image-pixel seed, splitting touching components when possible."""
    if image is None or image.ndim != 3 or image.shape[0] < 20 or image.shape[1] < 20:
        raise EggMeasureError("That image is too small to measure an egg.")
    height, width = image.shape[:2]
    seed_x, seed_y = int(round(x)), int(round(y))
    if seed_x < 0 or seed_y < 0 or seed_x >= width or seed_y >= height:
        raise EggMeasureError("Select a point inside an egg.")
    blurred = cv2.GaussianBlur(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    candidates: list[dict] = []
    for mask in _masks(blurred, image):
        component = _component_at_point(mask, seed_x, seed_y)
        if component is None:
            continue
        component_mask, left, top = component
        component_height, component_width = component_mask.shape
        distance = cv2.distanceTransform(component_mask, cv2.DIST_L2, 5)
        maximum = float(distance.max())
        if maximum < 4:
            continue
        peaks = _distance_peaks(distance)
        if len(peaks) <= 1:
            full_mask = np.zeros((height, width), dtype=np.uint8)
            full_mask[top:top + component_height, left:left + component_width] = component_mask
            candidates.extend(
                item for item in _contours(full_mask, width, height) if _contains_point(item, seed_x, seed_y)
            )
            continue
        if len(peaks) > 9:
            continue
        seed_x_local, seed_y_local = seed_x - left, seed_y - top
        selected_peak = min(
            range(len(peaks)),
            key=lambda index: (peaks[index][0] - seed_x_local) ** 2 + (peaks[index][1] - seed_y_local) ** 2,
        )
        y_coordinates = np.arange(component_height, dtype=np.float32)[:, None]
        x_coordinates = np.arange(component_width, dtype=np.float32)[None, :]
        nearest_peak = np.zeros(component_mask.shape, dtype=np.uint8)
        nearest_distance = np.full(component_mask.shape, np.inf, dtype=np.float32)
        for peak_id, (peak_x, peak_y) in enumerate(peaks):
            peak_distance = (x_coordinates - peak_x) ** 2 + (y_coordinates - peak_y) ** 2
            closer = (component_mask > 0) & (peak_distance < nearest_distance)
            nearest_peak[closer] = peak_id
            nearest_distance[closer] = peak_distance[closer]
        segment = np.uint8((component_mask > 0) & (nearest_peak == selected_peak)) * 255
        full_mask = np.zeros((height, width), dtype=np.uint8)
        full_mask[top:top + component_height, left:left + component_width] = segment
        candidates.extend(
            item for item in _contours(full_mask, width, height) if _contains_point(item, seed_x, seed_y)
        )
    # Several threshold masks can overlap around neighboring eggs. Rank by the
    # clicked point before non-maximum suppression; doing suppression by score
    # first can discard the clicked egg in favor of a stronger nearby mask.
    candidates = _separate(candidates, prefer_point=(seed_x, seed_y))
    if not candidates:
        raise EggMeasureError(
            "The selected egg could not be separated reliably. Try a point farther inside the egg."
        )
    scale = float(pixels_per_mm) if pixels_per_mm and pixels_per_mm > 0 else None
    warnings = [] if scale is not None else [
        "Measurements are in pixels. Set pixels per mm in Settings to convert them to millimetres."
    ]
    return {
        "ok": True,
        "unit": "px",
        "pixels_per_mm": None if scale is None else round(scale, 4),
        "definition": DEFINITION,
        "warnings": warnings,
        "eggs": [_publish(candidates[0], scale)],
    }


def _component_at_point(mask: np.ndarray, x: int, y: int) -> tuple[np.ndarray, int, int] | None:
    count, labels, stats, _ = cv2.connectedComponentsWithStats(np.uint8(mask > 0), 8)
    label = int(labels[y, x])
    if label == 0:
        # Only tolerate a tiny thresholding gap. A broad snap can attach a click
        # on tray/background to an unrelated speck and create a false correction.
        radius = 2
        left, right = max(0, x - radius), min(mask.shape[1], x + radius + 1)
        top, bottom = max(0, y - radius), min(mask.shape[0], y + radius + 1)
        ys, xs = np.nonzero(labels[top:bottom, left:right])
        if not len(xs):
            return None
        distances = (xs + left - x) ** 2 + (ys + top - y) ** 2
        nearest = int(np.argmin(distances))
        if distances[nearest] > radius * radius:
            return None
        label = int(labels[ys[nearest] + top, xs[nearest] + left])
    if label <= 0 or label >= count:
        return None
    left, top, width, height, area = stats[label]
    if area < mask.size * _MIN_COMPONENT_AREA_RATIO or area > mask.size * 0.8:
        return None
    return np.uint8(labels[top:top + height, left:left + width] == label) * 255, int(left), int(top)


def _distance_peaks(distance: np.ndarray) -> list[tuple[int, int]]:
    maximum = float(distance.max())
    window = max(9, min(51, int(maximum * 0.5) | 1))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (window, window))
    local_maximum = cv2.dilate(distance, kernel)
    peak_mask = np.uint8((distance >= maximum * 0.45) & (distance >= local_maximum - 0.5))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(peak_mask, 8)
    peaks: list[tuple[int, int]] = []
    for label in range(1, count):
        if stats[label, cv2.CC_STAT_AREA] < 2:
            continue
        ys, xs = np.nonzero(labels == label)
        best = int(np.argmax(distance[ys, xs]))
        peak = (int(xs[best]), int(ys[best]))
        if any(math.hypot(peak[0] - other[0], peak[1] - other[1]) < maximum * 0.55 for other in peaks):
            continue
        peaks.append(peak)
    return peaks


def _contains_point(item: dict, x: int, y: int) -> bool:
    contour = item.get("contour")
    return contour is not None and cv2.pointPolygonTest(contour, (float(x), float(y)), False) >= 0


def _masks(blur: np.ndarray, image: np.ndarray) -> list[np.ndarray]:
    height, width = blur.shape
    kernel = _kernel(height, width)
    masks = []
    _, otsu = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if float(otsu.mean()) > 127:
        otsu = cv2.bitwise_not(otsu)
    masks.append(_clean(otsu, kernel))
    border = np.concatenate((blur[0, :], blur[-1, :], blur[:, 0], blur[:, -1]))
    level = float(np.median(border))
    if level < 60:
        cutoff = int(max(level + 28, 16))
        _, dark = cv2.threshold(blur, cutoff, 255, cv2.THRESH_BINARY)
        masks.append(_clean(dark, kernel))
    lab = cv2.cvtColor(cv2.GaussianBlur(image, (5, 5), 0), cv2.COLOR_BGR2LAB).astype(np.float32)
    border = np.concatenate((lab[0, :, :], lab[-1, :, :], lab[:, 0, :], lab[:, -1, :]))
    background = np.median(border, axis=0)
    border_distance = np.linalg.norm(border - background, axis=1)
    border_median = float(np.median(border_distance))
    border_deviation = float(np.median(np.abs(border_distance - border_median)))
    contrast_threshold = max(10.0, border_median + 4.0 * 1.4826 * border_deviation)
    color_distance = np.linalg.norm(lab - background, axis=2)
    contrast = np.uint8(color_distance > contrast_threshold) * 255
    masks.append(_clean(contrast, kernel))
    return masks


def _kernel(height: int, width: int) -> np.ndarray:
    size = max(3, (min(height, width) // 80) | 1)
    if size % 2 == 0:
        size += 1
    size = min(size, 9)
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))


def _clean(mask: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    opened = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    return cv2.morphologyEx(opened, cv2.MORPH_CLOSE, kernel)


def _split_components(mask: np.ndarray, width: int, height: int) -> tuple[list[dict], list[tuple[int, int, int, int]]]:
    count, labels, stats, _ = cv2.connectedComponentsWithStats(np.uint8(mask > 0), 8)
    image_area = float(width * height)
    candidates: list[dict] = []
    split_components: list[tuple[int, int, int, int]] = []
    for component_id in range(1, count):
        x, y, box_width, box_height, area = stats[component_id]
        if area < image_area * _MIN_COMPONENT_AREA_RATIO or area > image_area * 0.8:
            continue
        component = np.uint8(labels[y:y + box_height, x:x + box_width] == component_id) * 255
        distance = cv2.distanceTransform(component, cv2.DIST_L2, 5)
        maximum = float(distance.max())
        if maximum < 4:
            continue
        seed_count, seeds = cv2.connectedComponents(np.uint8(distance >= maximum * 0.32), 8)
        if seed_count <= 2 or seed_count > 9:
            continue

        markers = np.ones(component.shape, dtype=np.int32)
        markers[component > 0] = 0
        for seed_id in range(1, seed_count):
            markers[seeds == seed_id] = seed_id + 1
        elevation = np.uint8(np.clip(255 * (1 - distance / maximum), 0, 255))
        cv2.watershed(cv2.cvtColor(elevation, cv2.COLOR_GRAY2BGR), markers)

        component_candidates: list[dict] = []
        for seed_id in range(2, seed_count + 1):
            segment = _clean(
                np.uint8(markers == seed_id) * 255,
                _kernel(box_height, box_width),
            )
            full_mask = np.zeros((height, width), dtype=np.uint8)
            full_mask[y:y + box_height, x:x + box_width] = segment
            component_candidates.extend(
                candidate
                for candidate in _contours(full_mask, width, height)
                if "bbox" in candidate
            )
        if len(component_candidates) >= 2:
            candidates.extend(component_candidates)
            split_components.append((int(x), int(y), int(box_width), int(box_height)))
    return candidates, split_components


def _contours(mask: np.ndarray, width: int, height: int) -> list[dict]:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    image_area = float(width * height)
    found = []
    for contour in contours:
        area = float(cv2.contourArea(contour))
        if area < image_area * _MIN_COMPONENT_AREA_RATIO or area > image_area * 0.8:
            continue
        if len(contour) < 20:
            continue
        points = contour.reshape(-1, 2)
        edge = (
            (points[:, 0] <= 1)
            | (points[:, 0] >= width - 2)
            | (points[:, 1] <= 1)
            | (points[:, 1] >= height - 2)
        )
        if float(edge.mean()) > 0.015:
            found.append({"clipped": True})
            continue
        hull = float(cv2.contourArea(cv2.convexHull(contour))) or 1.0
        solidity = area / hull
        if solidity < 0.9:
            continue
        ellipse = cv2.fitEllipse(contour)
        (center_x, center_y), (axis_w, axis_h), angle = ellipse
        major = float(max(axis_w, axis_h))
        minor = float(min(axis_w, axis_h))
        if minor < 8 or major > math.hypot(width, height):
            continue
        aspect = major / minor
        if aspect < 1.02 or aspect > 2.45:
            continue
        ellipse_area = math.pi * (major / 2) * (minor / 2)
        ratio = area / ellipse_area if ellipse_area else 0
        if ratio < 0.8 or ratio > 1.15:
            continue
        moments = cv2.moments(contour)
        if moments["m00"] <= 0:
            continue
        centroid_x = moments["m10"] / moments["m00"]
        centroid_y = moments["m01"] / moments["m00"]
        if math.hypot(centroid_x - center_x, centroid_y - center_y) > major * 0.2:
            continue
        x, y, box_w, box_h = cv2.boundingRect(contour)
        penalty = min(abs(aspect - 1.45) / 2.2, 0.35)
        score = solidity * min(ratio, 1.0) * (1 - penalty)
        if score < 0.55:
            continue
        found.append(
            {
                "score": float(score),
                "contour": contour,
                "center": (float(center_x), float(center_y)),
                "major": major,
                "minor": minor,
                "angle": _major_angle(float(axis_w), float(axis_h), float(angle)),
                "area": area,
                "perimeter": float(cv2.arcLength(contour, True)),
                "bbox": (int(x), int(y), int(box_w), int(box_h)),
                "mask_shape": (height, width),
            }
        )
    return found


def _major_angle(axis_w: float, axis_h: float, angle: float) -> float:
    major = angle + (90.0 if axis_h > axis_w else 0.0)
    while major >= 90:
        major -= 180
    while major < -90:
        major += 180
    return major


def _separate(
    found: list[dict],
    prefer_point: tuple[int, int] | None = None,
) -> list[dict]:
    candidates = (item for item in found if "bbox" in item)
    if prefer_point is None:
        ordered = sorted(candidates, key=lambda item: item["score"], reverse=True)
    else:
        point_x, point_y = prefer_point
        ordered = sorted(
            candidates,
            key=lambda item: (
                (item["center"][0] - point_x) ** 2 + (item["center"][1] - point_y) ** 2,
                -item["score"],
            ),
        )
    kept: list[dict] = []
    for item in ordered:
        if any(_iou(item["bbox"], other["bbox"]) > 0.45 for other in kept):
            continue
        kept.append(item)
    return kept


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
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


def _publish(item: dict, scale: float | None) -> dict:
    center_x, center_y = item["center"]
    major = item["major"]
    minor = item["minor"]
    angle = item["angle"]
    x, y, box_w, box_h = item["bbox"]
    contour = _simplify(item["contour"])
    contour_points = np.asarray(contour, dtype=np.float64)
    direction = np.array((math.cos(math.radians(angle)), math.sin(math.radians(angle))))
    perpendicular = np.array((-direction[1], direction[0]))
    relative = contour_points - np.array((center_x, center_y))
    projected_length = float(np.ptp(relative @ direction))
    projected_width = float(np.ptp(relative @ perpendicular))
    length_mm, width_mm, millimetres = _millimetres(projected_length, projected_width, scale)
    sections = _cross_sections(item, scale)
    return {
        "score": round(item["score"], 3),
        "contour": contour,
        "ellipse": {
            "center_x": round(center_x, 2),
            "center_y": round(center_y, 2),
            "major_px": round(major, 2),
            "minor_px": round(minor, 2),
            "angle_deg": round(angle, 2),
        },
        "bbox": {"x": x, "y": y, "width": box_w, "height": box_h},
        "area_px": round(item["area"], 1),
        "perimeter_px": round(item["perimeter"], 1),
        "length_px": round(projected_length, 2),
        "width_px": round(projected_width, 2),
        "length_mm": length_mm,
        "width_mm": width_mm,
        "millimetres": millimetres,
        "cross_sections": sections,
        "definition": DEFINITION,
    }


def _millimetres(major: float, minor: float, scale: float | None) -> tuple[float | None, float | None, bool]:
    if scale is None:
        return None, None, False
    length = major / scale
    width = minor / scale
    if length <= 0 or width <= 0:
        return None, None, False
    return round(length, 2), round(width, 2), True


def _cross_sections(item: dict, scale: float | None) -> list[dict]:
    height, width = item["mask_shape"]
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.drawContours(mask, [item["contour"]], -1, 255, thickness=-1)
    center_x, center_y = item["center"]
    angle = math.radians(item["angle"])
    axis = (math.cos(angle), math.sin(angle))
    perpendicular = (-axis[1], axis[0])
    half = item["major"] / 2
    limit = int(item["minor"]) + 4
    samples = []
    for position in _SECTIONS:
        origin = (
            center_x + axis[0] * half * (2 * position - 1),
            center_y + axis[1] * half * (2 * position - 1),
        )
        span = _span(mask, origin, perpendicular, limit)
        if span is None:
            continue
        width_mm = None if scale is None else round(span / scale, 2)
        if width_mm is not None and width_mm <= 0:
            width_mm = None
        samples.append(
            {
                "position": position,
                "width_px": round(span, 2),
                "width_mm": width_mm,
            }
        )
    return samples


def _span(mask: np.ndarray, origin: tuple[float, float], direction: tuple[float, float], limit: int) -> float | None:
    height, width = mask.shape
    ox, oy = origin
    ix, iy = int(round(ox)), int(round(oy))
    if ix < 0 or iy < 0 or ix >= width or iy >= height or mask[iy, ix] == 0:
        return None

    def walk(sign: int) -> float:
        last = 0.0
        for step in range(1, max(2, limit)):
            x = int(round(ox + sign * direction[0] * step))
            y = int(round(oy + sign * direction[1] * step))
            if x < 0 or y < 0 or x >= width or y >= height or mask[y, x] == 0:
                return last
            last = float(step)
        return last

    span = walk(-1) + walk(1)
    if span < 2:
        return None
    return span


def _simplify(contour: np.ndarray) -> list[list[float]]:
    perimeter = float(cv2.arcLength(contour, True)) or 1.0
    approx = cv2.approxPolyDP(contour, 0.004 * perimeter, True)
    points = approx.reshape(-1, 2)
    if len(points) > 80:
        step = int(math.ceil(len(points) / 80))
        points = points[::step]
    return [[round(float(x), 2), round(float(y), 2)] for x, y in points]
