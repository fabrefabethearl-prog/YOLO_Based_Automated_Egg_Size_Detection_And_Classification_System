import cv2
import numpy as np

from backend.services.egg_geometry import EggMeasureError, _separate, eggs_from_boxes, measure_egg_at_point, measure_eggs


def _jpeg(image: np.ndarray) -> bytes:
    ok, buffer = cv2.imencode(".jpg", image)
    assert ok
    return buffer.tobytes()


def _egg(angle: float = 20, center=(320, 240), radii=(80, 55), color=(230, 230, 230), background=(12, 12, 12)) -> np.ndarray:
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    image[:] = background
    cv2.ellipse(image, center, radii, angle, 0, 360, color, -1)
    return image


def test_rotated_egg_on_black_returns_contour_and_ellipse():
    image = _egg()
    body = measure_eggs(image, pixels_per_mm=5)
    assert body["ok"] is True
    assert body["unit"] == "px"
    assert len(body["eggs"]) == 1
    egg = body["eggs"][0]
    assert abs(egg["length_px"] - 160) < 14
    assert abs(egg["width_px"] - 110) < 14
    assert egg["length_px"] > egg["width_px"]
    assert abs(egg["ellipse"]["angle_deg"] - 20) < 12
    assert len(egg["contour"]) >= 8
    assert egg["area_px"] > 1000
    assert egg["perimeter_px"] > 100
    assert egg["length_mm"] == round(egg["length_px"] / 5, 2)
    assert any(sample["position"] == 0.5 for sample in egg["cross_sections"])
    mid = next(sample for sample in egg["cross_sections"] if sample["position"] == 0.5)
    assert abs(mid["width_px"] - 110) < 18
    assert egg["bbox"]["width"] > 0


def test_low_pixels_per_mm_still_returns_millimetres():
    image = _egg()
    egg = measure_eggs(image, pixels_per_mm=0.7667)["eggs"][0]
    assert egg["length_mm"] > 120
    assert abs(egg["length_mm"] - egg["length_px"] / 0.7667) < 0.05
    assert abs(egg["width_mm"] - egg["width_px"] / 0.7667) < 0.05


def test_dark_shell_is_still_separated_from_black():
    image = _egg(color=(62, 64, 60), background=(8, 8, 8), angle=0, radii=(70, 48))
    egg = measure_eggs(image)["eggs"][0]
    assert abs(egg["length_px"] - 140) < 16
    assert egg["length_mm"] is None


def test_egg_small_relative_to_high_resolution_frame_is_detected_and_measured():
    image = np.full((1200, 1600, 3), (8, 8, 8), dtype=np.uint8)
    cv2.ellipse(image, (800, 600), (65, 45), 15, 0, 360, (180, 180, 180), -1)

    eggs = measure_eggs(image, pixels_per_mm=5)["eggs"]

    assert len(eggs) == 1
    egg = eggs[0]
    assert abs(egg["length_px"] - 130) < 6
    assert abs(egg["width_px"] - 90) < 6
    assert egg["length_mm"] == round(egg["length_px"] / 5, 2)
    assert egg["width_mm"] == round(egg["width_px"] / 5, 2)
    assert abs(egg["bbox"]["width"] - 130) < 6
    assert abs(egg["bbox"]["height"] - 90) < 6
    assert len(egg["contour"]) >= 8


def test_equal_brightness_egg_is_detected_by_color_contrast():
    image = _egg(color=(0, 0, 57), background=(150, 0, 0))
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    assert gray[0, 0] == gray[240, 320]

    egg = measure_eggs(image)["eggs"][0]

    assert abs(egg["length_px"] - 160) < 14
    assert abs(egg["width_px"] - 110) < 14
    assert len(egg["contour"]) >= 8


def test_touching_eggs_are_separated_before_measurement():
    image = np.full((320, 480, 3), (8, 8, 8), dtype=np.uint8)
    cv2.ellipse(image, (210, 160), (70, 48), -10, 0, 360, (230, 230, 230), -1)
    cv2.ellipse(image, (350, 160), (70, 48), 10, 0, 360, (230, 230, 230), -1)

    eggs = measure_eggs(image)["eggs"]

    assert len(eggs) == 2
    for egg in eggs:
        assert abs(egg["length_px"] - 140) < 25
        assert abs(egg["width_px"] - 96) < 20


def test_guided_measurement_selects_each_egg_from_an_overlapping_pair():
    image = np.full((320, 480, 3), (8, 8, 8), dtype=np.uint8)
    cv2.ellipse(image, (205, 160), (70, 48), -8, 0, 360, (230, 230, 230), -1)
    cv2.ellipse(image, (315, 160), (70, 48), 8, 0, 360, (230, 230, 230), -1)

    left = measure_egg_at_point(image, 205, 160)["eggs"][0]
    right = measure_egg_at_point(image, 315, 160)["eggs"][0]

    assert left["ellipse"]["center_x"] < right["ellipse"]["center_x"]
    assert abs(left["length_px"] - 140) < 25
    assert abs(right["length_px"] - 140) < 25
    assert left["bbox"]["x"] + left["bbox"]["width"] <= right["bbox"]["x"] + right["bbox"]["width"]


def test_guided_measurement_rejects_background_click():
    image = _egg()

    try:
        measure_egg_at_point(image, 10, 10)
        raise AssertionError("a background click should not produce an egg")
    except EggMeasureError as exc:
        assert "selected egg" in exc.message.lower() or "inside an egg" in exc.message.lower()


def test_guided_candidate_dedup_keeps_the_contour_nearest_to_the_click():
    nearest = {"bbox": (0, 0, 30, 30), "center": (10.0, 10.0), "score": 0.65}
    stronger = {"bbox": (2, 2, 30, 30), "center": (30.0, 30.0), "score": 0.99}

    selected = _separate([stronger, nearest], prefer_point=(10, 10))

    assert selected == [nearest]


def test_detector_boxes_become_one_oval_per_egg():
    body = eggs_from_boxes(
        (300, 400, 3),
        [((40, 30, 80, 120), 0.96), ((180, 40, 90, 70), 0.91)],
        pixels_per_mm=4,
    )
    assert len(body["eggs"]) == 2
    tall, wide = body["eggs"]
    assert tall["ellipse"]["angle_deg"] == 90
    assert tall["length_px"] == 120
    assert tall["width_px"] == 80
    assert tall["length_mm"] == 30
    assert wide["ellipse"]["angle_deg"] == 0
    assert wide["length_px"] == 90
    assert len(tall["contour"]) >= 8


def test_blank_and_clipped_eggs_are_rejected():
    blank = np.zeros((480, 640, 3), dtype=np.uint8)
    blank[:] = (10, 10, 10)
    try:
        measure_eggs(blank)
        raise AssertionError("blank image should fail")
    except EggMeasureError as exc:
        assert "background" in exc.message
    clipped = _egg(center=(30, 240), radii=(80, 55), angle=0)
    try:
        measure_eggs(clipped)
        raise AssertionError("clipped egg should fail")
    except EggMeasureError as exc:
        assert "cut off" in exc.message


def test_auto_measure_does_not_save_until_confirmed(client, auth):
    from tests.test_dataset_prepare import upload_class

    image = _egg()
    response = upload_class(client, auth, "small", "small", count=1, blob=lambda _mark: _jpeg(image))
    assert response.status_code == 200, response.text
    listed = client.get("/api/dataset/annotate/small", headers=auth).json()
    name = listed["images"][0]["name"]
    detected = client.post(f"/api/dataset/annotate/small/{name}/auto", headers=auth)
    assert detected.status_code == 200, detected.text
    egg = detected.json()["eggs"][0]
    missing = client.get(f"/api/dataset/annotate/small/{name}/mark", headers=auth)
    assert missing.status_code == 404
    saved = client.put(
        f"/api/dataset/annotate/small/{name}/mark",
        headers=auth,
        json={
            "start_x": egg["bbox"]["x"],
            "start_y": egg["bbox"]["y"],
            "end_x": egg["bbox"]["x"] + egg["bbox"]["width"],
            "end_y": egg["bbox"]["y"] + egg["bbox"]["height"],
            "length_mm": 40,
            "width_mm": 25,
            "calibration": "manual",
            "center_x": egg["ellipse"]["center_x"],
            "center_y": egg["ellipse"]["center_y"],
            "angle_deg": egg["ellipse"]["angle_deg"],
            "major_px": egg["ellipse"]["major_px"],
            "minor_px": egg["ellipse"]["minor_px"],
            "area_px": egg["area_px"],
            "perimeter_px": egg["perimeter_px"],
            "contour": egg["contour"],
            "cross_sections": egg["cross_sections"],
        },
    )
    assert saved.status_code == 200, saved.text
    mark = saved.json()["ovals"][0]
    assert abs(mark["measurement"]["pixel_length"] - egg["length_px"]) < 0.2
    assert mark["geometry"]["contour"]
    assert mark["geometry"]["major_px"] == egg["ellipse"]["major_px"]


def test_guided_annotation_endpoint_returns_only_the_selected_egg(client, auth):
    from tests.test_dataset_prepare import upload_class

    image = np.full((320, 480, 3), (8, 8, 8), dtype=np.uint8)
    cv2.ellipse(image, (205, 160), (70, 48), -8, 0, 360, (230, 230, 230), -1)
    cv2.ellipse(image, (315, 160), (70, 48), 8, 0, 360, (230, 230, 230), -1)
    upload = upload_class(client, auth, "small", "small", count=1, blob=lambda _mark: _jpeg(image))
    assert upload.status_code == 200, upload.text
    item = client.get("/api/dataset/annotate/small", headers=auth).json()["images"][0]

    left = client.post(
        f"/api/dataset/annotate/small/{item['name']}/guide",
        headers=auth,
        json={"x": 205, "y": 160},
    )
    right = client.post(
        f"/api/dataset/annotate/small/{item['name']}/guide",
        headers=auth,
        json={"x": 315, "y": 160},
    )

    assert left.status_code == 200, left.text
    assert right.status_code == 200, right.text
    left_egg, = left.json()["eggs"]
    right_egg, = right.json()["eggs"]
    assert left_egg["ellipse"]["center_x"] < right_egg["ellipse"]["center_x"]
    assert left_egg["length_px"] > left_egg["width_px"]
    assert right_egg["length_px"] > right_egg["width_px"]
    assert client.get(
        f"/api/dataset/annotate/small/{item['name']}/mark",
        headers=auth,
    ).status_code == 404
