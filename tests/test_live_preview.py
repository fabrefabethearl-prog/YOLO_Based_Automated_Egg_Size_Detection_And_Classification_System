"""Live preview does not save, and a saved scan can keep the stable reading."""

import threading

import cv2
import numpy as np

from backend.services.infer import _LOCK


def ellipse_jpeg(width=640, height=480) -> bytes:
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[:] = (32, 48, 36)
    cv2.ellipse(image, (width // 2, height // 2), (width // 8, height // 4), 0, 0, 360, (236, 240, 244), -1)
    ok, buffer = cv2.imencode(".jpg", image)
    assert ok
    return buffer.tobytes()


def test_preview_detects_without_saving_or_logging(client, auth):
    before_scans = client.get("/api/scans", headers=auth).json()["total"]
    before_logs = client.get("/api/logs", headers=auth).json()["total"]
    response = client.post(
        "/api/scan/preview",
        files={"file": ("frame.jpg", ellipse_jpeg(), "image/jpeg")},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["detected"] is True
    assert body["mode"] == "mock"
    assert body["reason"] is None
    assert body["class"]
    assert body["area_mm2"] > 0
    assert body["length_mm"] >= body["width_mm"]
    assert body["geometry"]["source"] == "contour"
    assert body["geometry"]["ellipse"]["major_ratio"] > 0
    assert body["geometry"]["ellipse_major_px"] >= body["geometry"]["ellipse_minor_px"]
    assert body["aspect_ratio"] > 1
    assert body["area_px"] > 0
    box = body["bbox"]
    assert 0 <= box["x"] <= 1
    assert 0 <= box["y"] <= 1
    assert 0 < box["w"] <= 1
    assert 0 < box["h"] <= 1
    assert body["inference_ms"] >= 0
    assert client.get("/api/scans", headers=auth).json()["total"] == before_scans
    assert client.get("/api/logs", headers=auth).json()["total"] == before_logs


def test_preview_reports_every_egg_in_the_frame(client, auth):
    image = np.zeros((480, 1280, 3), dtype=np.uint8)
    image[:] = (32, 48, 36)
    eggs = (
        (120, 240, 80, 180),
        (330, 240, 90, 172),
        (560, 240, 100, 165),
        (805, 240, 105, 167),
        (1065, 240, 115, 166),
    )
    for center_x, center_y, radius_x, radius_y in eggs:
        cv2.ellipse(image, (center_x, center_y), (radius_x, radius_y), 0, 0, 360, (236, 240, 244), -1)
    ok, buffer = cv2.imencode(".jpg", image)
    assert ok
    before = client.get("/api/scans", headers=auth).json()["total"]
    response = client.post(
        "/api/scan/preview",
        files={"file": ("frame.jpg", buffer.tobytes(), "image/jpeg")},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["detected"] is True
    assert body["egg_count"] == 5
    assert len(body["detections"]) == 5
    names = [item["class"] for item in body["detections"]]
    assert names == ["Small", "Medium", "Large", "Extra Large", "Jumbo"]
    assert [item["id"] for item in body["detections"]] == [1, 2, 3, 4, 5]
    for item in body["detections"]:
        assert item["confidence"] >= 0.5
        assert 0 <= item["bbox"]["x"] <= 1
        assert item["bbox"]["w"] > 0
    counts = {row["class"]: row["count"] for row in body["summary"]}
    assert counts == {"Small": 1, "Medium": 1, "Large": 1, "Extra Large": 1, "Jumbo": 1}
    assert client.get("/api/scans", headers=auth).json()["total"] == before


def test_preview_reports_no_egg(client, auth):
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    ok, buffer = cv2.imencode(".jpg", image)
    assert ok
    response = client.post(
        "/api/scan/preview",
        files={"file": ("blank.jpg", buffer.tobytes(), "image/jpeg")},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["detected"] is False
    assert body["reason"] == "no egg"
    assert body["class"] is None
    assert body["area_mm2"] is None


def test_preview_rejects_oversize_frames(client, auth):
    raw = b"\xff\xd8" + (b"0" * (2 * 1024 * 1024))
    response = client.post(
        "/api/scan/preview",
        files={"file": ("big.jpg", raw, "image/jpeg")},
        headers=auth,
    )
    assert response.status_code == 413


def test_preview_reports_low_confidence(client, auth):
    client.put("/api/settings", headers=auth, json={
        "confidence": 0.999,
        "pixels_per_mm": 5,
        "classification_mode": "dimensions",
        "camera_index": 0,
    })
    response = client.post(
        "/api/scan/preview",
        files={"file": ("frame.jpg", ellipse_jpeg(), "image/jpeg")},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["detected"] is False
    assert body["reason"] == "low confidence"
    assert body["confidence"] > 0


def test_preview_reports_missing_calibration(client, auth, monkeypatch):
    import backend.routers.scans as scans

    original = scans.get_settings

    def uncalibrated(db):
        current = original(db)
        current["pixels_per_mm"] = 0
        return current

    monkeypatch.setattr(scans, "get_settings", uncalibrated)
    response = client.post(
        "/api/scan/preview",
        files={"file": ("frame.jpg", ellipse_jpeg(), "image/jpeg")},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["detected"] is True
    assert body["reason"] == "calibration missing"
    assert body["unit"] == "px"
    assert body["length_mm"] is None
    assert body["width_mm"] is None
    assert body["area_mm2"] is None
    assert body["length_px"] >= body["width_px"]
    assert body["geometry"]["source"] == "contour"


def test_preview_keeps_model_detection_when_contour_fit_fails(client, auth, monkeypatch):
    import backend.routers.scans as scans

    monkeypatch.setattr(scans, "_fit_detection_geometry", lambda *_args: None)
    response = client.post(
        "/api/scan/preview",
        files={"file": ("frame.jpg", ellipse_jpeg(), "image/jpeg")},
        headers=auth,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["detected"] is True
    assert body["geometry"] is None
    assert body["measurement_source"] == "bounding box"
    assert body["length_px"] >= body["width_px"]
    assert body["area_px"] is None


def test_uncalibrated_model_preview_does_not_map_color_classes_to_size_classes(client, monkeypatch):
    from types import SimpleNamespace

    from backend.routers.scans import _preview_frame
    from backend.services.detector import EggHit, detector

    image = np.zeros((480, 640, 3), dtype=np.uint8)
    image[:] = (32, 48, 36)
    cv2.ellipse(image, (320, 240), (80, 110), 0, 0, 360, (236, 240, 244), -1)
    categories = [SimpleNamespace(name=name) for name in ("Small", "Medium", "Large", "Extra Large", "Jumbo")]
    monkeypatch.setattr(detector, "mock", False)
    monkeypatch.setattr(detector, "model", SimpleNamespace(names={0: "white-egg", 1: "brown-egg"}))

    hit = EggHit(0.92, (240, 130, 160, 220), "white-egg", 0)
    result = _preview_frame(
        [hit],
        hit,
        12.5,
        image,
        {
            "pixels_per_mm": 0,
            "classification_mode": "model",
            "out_of_range": "nearest",
        },
        categories,
    )

    assert result["detected"] is True
    assert result["class"] is None
    assert result["class_id"] is None
    assert result["model_class"] == "white-egg"
    assert result["model_class_id"] == 0
    assert result["classification_reason"] == "unmapped model class"
    assert result["unit"] == "px"
    assert result["length_mm"] is None
    assert result["geometry"]["source"] == "contour"


def test_preview_when_detector_is_busy(client, auth):
    held = threading.Event()
    release = threading.Event()

    def hold():
        _LOCK.acquire()
        held.set()
        release.wait(3)
        _LOCK.release()

    worker = threading.Thread(target=hold)
    worker.start()
    assert held.wait(2)
    try:
        response = client.post(
            "/api/scan/preview",
            files={"file": ("frame.jpg", ellipse_jpeg(), "image/jpeg")},
            headers=auth,
        )
    finally:
        release.set()
        worker.join(3)
    assert response.status_code == 429


def test_save_keeps_stable_override_and_both_images(client, auth):
    response = client.post(
        "/api/scan",
        files={"file": ("frame.jpg", ellipse_jpeg(), "image/jpeg")},
        data={"confidence": "0.94", "length_mm": "57", "width_mm": "44"},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["saved"] is True
    assert body["area_mm2"] == 2508
    assert body["confidence"] == 0.94
    assert body["length_mm"] == 57
    assert body["width_mm"] == 44
    assert body["predicted_class"] == "Medium"
    from backend.paths import scans_dir

    frames = list(scans_dir().glob("*-frame.jpg"))
    annotated = [path for path in scans_dir().glob("*.jpg") if not path.name.endswith("-frame.jpg")]
    assert len(frames) == 1
    assert len(annotated) == 1
