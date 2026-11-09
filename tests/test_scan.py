import base64

import cv2
import numpy as np

from backend.services.size import class_for_area


def ellipse_jpeg() -> bytes:
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    image[:] = (32, 48, 36)
    cv2.ellipse(image, (320, 240), (80, 110), 0, 0, 360, (236, 240, 244), -1)
    ok, buffer = cv2.imencode(".jpg", image)
    assert ok
    return buffer.tobytes()


def blank_jpeg() -> bytes:
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    ok, buffer = cv2.imencode(".jpg", image)
    assert ok
    return buffer.tobytes()


def test_overview_starts_empty(client, auth):
    response = client.get("/api/stats/overview", headers=auth)
    assert response.status_code == 200
    body = response.json()
    assert body["total_scanned"] == 0
    assert body["accuracy"] is None
    assert body["avg_confidence"] is None
    assert body["avg_inference_ms"] is None
    assert len(body["per_day"]) == 14
    assert body["mock_mode"] is True
    names = [item["name"] for item in body["class_distribution"]]
    assert names == ["Small", "Medium", "Large", "Extra Large", "Jumbo"]
    assert [item["count"] for item in body["class_distribution"]] == [0, 0, 0, 0, 0]
    model = client.get("/api/stats/model", headers=auth)
    assert model.status_code == 200
    confusion = model.json()["confusion"]
    assert confusion["labels"] == names
    assert len(confusion["matrix"]) == 5
    assert all(len(row) == 5 for row in confusion["matrix"])
    public = client.get("/api/size-classes")
    assert public.status_code == 200
    assert [row["name"] for row in public.json()["categories"]] == names


def test_mock_scan_saves_area_and_class(client, auth):
    response = client.post(
        "/api/scan",
        files={"file": ("egg.jpg", ellipse_jpeg(), "image/jpeg")},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["detected"] is True
    assert body["saved"] is True
    assert body["mock"] is True
    assert body["id"]
    assert body["bbox"]["width"] > 0
    assert body["annotated_image"].startswith("data:image/jpeg;base64,")
    assert body["length_mm"] >= body["width_mm"]
    assert body["area_mm2"] == round(body["length_mm"] * body["width_mm"], 2)
    categories = client.get("/api/categories", headers=auth).json()["categories"]
    assert body["predicted_class"] == class_for_area(body["area_mm2"], _as_cats(categories))
    assert body["actual_class"] is None
    assert "est_weight_g" not in body
    patched = client.patch(f"/api/scans/{body['id']}", json={"actual_class": "Large"}, headers=auth)
    assert patched.status_code == 200
    listed = client.get("/api/scans?correctness=miss", headers=auth)
    assert listed.status_code == 200
    assert listed.json()["total"] == (0 if body["predicted_class"] == "Large" else 1)


def test_scan_record_summary_uses_verified_labels_only(client, auth):
    created = client.post(
        "/api/scan",
        files={"file": ("egg.jpg", ellipse_jpeg(), "image/jpeg")},
        headers=auth,
    )
    assert created.status_code == 200 and created.json()["saved"] is True
    scan = created.json()
    before = client.get("/api/scans", headers=auth).json()
    assert before["total"] == 1
    verified = client.patch(
        f"/api/scans/{scan['id']}",
        json={"actual_class": scan["predicted_class"]},
        headers=auth,
    )
    assert verified.status_code == 200

    response = client.get("/api/scans?include_summary=true", headers=auth)
    assert response.status_code == 200
    assert response.json()["summary"] == {
        "total": 1,
        "verified": 1,
        "correct": 1,
        "incorrect": 0,
        "pending": 0,
    }
    assert response.json()["total"] == before["total"]


def test_base64_scan(client, auth):
    payload = {"image_base64": base64.b64encode(ellipse_jpeg()).decode("ascii")}
    response = client.post("/api/scan", json=payload, headers=auth)
    assert response.status_code == 200, response.text
    assert response.json()["detected"] is True


def test_blank_frame_is_not_saved(client, auth):
    response = client.post(
        "/api/scan",
        files={"file": ("blank.jpg", blank_jpeg(), "image/jpeg")},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["detected"] is False
    assert body["saved"] is False
    assert "egg" in body["message"].lower() or "confidence" in body["message"].lower()
    listed = client.get("/api/scans", headers=auth)
    assert listed.json()["total"] == 0
    logs = client.get("/api/logs?level=WARN", headers=auth)
    assert logs.json()["total"] >= 1


def test_low_confidence_is_not_saved(client, auth):
    saved = client.put(
        "/api/settings",
        json={
            "confidence": 0.999,
            "pixels_per_mm": 5,
            "classification_mode": "dimensions",
            "camera_index": 0,
        },
        headers=auth,
    )
    assert saved.status_code == 200, saved.text
    response = client.post(
        "/api/scan",
        files={"file": ("egg.jpg", ellipse_jpeg(), "image/jpeg")},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    assert response.json()["detected"] is False
    assert "confidence" in response.json()["message"].lower()
    assert client.get("/api/scans", headers=auth).json()["total"] == 0


def test_scan_requires_auth(client):
    response = client.post("/api/scan", files={"file": ("egg.jpg", ellipse_jpeg(), "image/jpeg")})
    assert response.status_code == 401


def test_category_change_recomputes_classes(client, auth):
    created = client.post(
        "/api/scan",
        files={"file": ("egg.jpg", ellipse_jpeg(), "image/jpeg")},
        headers=auth,
    )
    assert created.status_code == 200 and created.json()["detected"]
    updated = client.put(
        "/api/categories",
        json={
            "categories": [
                {"name": "Small", "min_area": 100, "color": "#5a9a6e"},
                {"name": "Medium", "min_area": 200, "color": "#2d6a4f"},
                {"name": "Large", "min_area": 300, "color": "#d99a2b"},
                {"name": "Extra Large", "min_area": 350, "color": "#c4733b"},
                {"name": "Jumbo", "min_area": 400, "color": "#a23d3d"},
            ]
        },
        headers=auth,
    )
    assert updated.status_code == 200, updated.text
    scan = client.get("/api/scans", headers=auth).json()["items"][0]
    assert scan["predicted_class"] == "Jumbo"


def test_category_minimums_must_increase(client, auth):
    response = client.put(
        "/api/categories",
        json={
            "categories": [
                {"name": "Small", "min_area": 10, "color": "#5a9a6e"},
                {"name": "Medium", "min_area": 10, "color": "#2d6a4f"},
                {"name": "Large", "min_area": 20, "color": "#d99a2b"},
                {"name": "Extra Large", "min_area": 25, "color": "#c4733b"},
                {"name": "Jumbo", "min_area": 30, "color": "#a23d3d"},
            ]
        },
        headers=auth,
    )
    assert response.status_code == 400
    assert "strictly increase" in response.json()["detail"]


def test_patch_rejects_an_unknown_size_class(client, auth):
    saved = client.post(
        "/api/scan",
        files={"file": ("egg.jpg", ellipse_jpeg(), "image/jpeg")},
        headers=auth,
    ).json()
    rejected = client.patch(f"/api/scans/{saved['id']}", json={"actual_class": "NotAClass"}, headers=auth)
    assert "Small" in rejected.json()["detail"]
    filtered = client.get("/api/scans?class_name=NotAClass", headers=auth)
    assert filtered.status_code == 422
    renamed = client.put(
        "/api/categories",
        json={"categories": [{"name": "NotAClass", "min_area": 0, "color": "#5a9a6e"}]},
        headers=auth,
    )
    assert renamed.status_code == 422
    assert "Small" in renamed.json()["detail"]


def test_fit_bands_previews_before_it_saves(client, auth):
    for name in ("Small", "Large"):
        for _ in range(6):
            saved = client.post(
                "/api/scan",
                files={"file": ("egg.jpg", ellipse_jpeg(), "image/jpeg")},
                headers=auth,
            ).json()
            patched = client.patch(f"/api/scans/{saved['id']}", json={"actual_class": name}, headers=auth)
            assert patched.status_code == 200
    preview = client.post("/api/settings/fit-bands", json={"confirm": False}, headers=auth)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["confirmed"] is False
    assert body["sample_count"] == 12
    assert body["before_accuracy"] is not None
    assert body["after_accuracy"] is not None
    current = client.get("/api/categories", headers=auth).json()["categories"]
    saved = client.post("/api/settings/fit-bands", json={"confirm": True}, headers=auth)
    assert saved.status_code == 200
    assert saved.json()["confirmed"] is True
    updated = client.get("/api/categories", headers=auth).json()["categories"]
    assert updated != current or body["after_accuracy"] == body["before_accuracy"]


def test_api_payloads_do_not_carry_weight_fields(client, auth):
    forbidden = ("weight", "gram", "mae")

    def walk(value, where):
        if isinstance(value, dict):
            for key, item in value.items():
                lowered = key.lower()
                assert not any(word in lowered for word in forbidden), where + " " + key
                walk(item, where)
        elif isinstance(value, list):
            for item in value:
                walk(item, where)
        elif isinstance(value, str):
            lowered = value.lower()
            assert "gram" not in lowered, where
            assert "weight" not in lowered, where

    scan = client.post(
        "/api/scan",
        files={"file": ("egg.jpg", ellipse_jpeg(), "image/jpeg")},
        headers=auth,
    ).json()
    walk(scan, "scan")
    walk(client.get("/api/stats/overview", headers=auth).json(), "overview")
    walk(client.get("/api/stats/model", headers=auth).json(), "model")
    walk(client.get("/api/settings", headers=auth).json(), "settings")
    walk(client.get("/api/categories", headers=auth).json(), "categories")
    response = client.post(
        "/api/dataset/upload",
        files={
            "image": ("egg.jpg", ellipse_jpeg(), "image/jpeg"),
            "label": ("egg.txt", b"0 0.500000 0.500000 0.250000 0.400000\n", "text/plain"),
        },
        data={"split": "train"},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    summary = client.get("/api/dataset", headers=auth).json()
    assert summary["splits"]["train"] == 1
    assert summary["per_class"][0]["name"] == "Small"
    assert summary["per_class"][0]["objects"] == 1


def test_reject_outside_the_five_classes_does_not_save(client, auth):
    saved = client.put(
        "/api/settings",
        json={
            "confidence": 0.5,
            "pixels_per_mm": 5,
            "classification_mode": "dimensions",
            "camera_index": 0,
            "out_of_range": "reject",
        },
        headers=auth,
    )
    assert saved.status_code == 200
    response = client.post(
        "/api/scan",
        files={"file": ("egg.jpg", ellipse_jpeg(), "image/jpeg")},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["saved"] is False
    assert body["detected"] is False
    assert body["message"] == "Out of range (outside Small to Jumbo)"
    assert body["area_mm2"] < 2250
    assert client.get("/api/scans", headers=auth).json()["total"] == 0
    nearest = client.put(
        "/api/settings",
        json={
            "confidence": 0.5,
            "pixels_per_mm": 5,
            "classification_mode": "dimensions",
            "camera_index": 0,
            "out_of_range": "nearest",
        },
        headers=auth,
    )
    assert nearest.status_code == 200
    kept = client.post(
        "/api/scan",
        files={"file": ("egg.jpg", ellipse_jpeg(), "image/jpeg")},
        headers=auth,
    ).json()
    assert kept["saved"] is True
    assert kept["predicted_class"] == "Small"


class _Cat:
    def __init__(self, name, min_area, sort_order):
        self.name = name
        self.min_area = min_area
        self.sort_order = sort_order


def _as_cats(rows):
    return [_Cat(row["name"], row["min_area"], row["sort_order"]) for row in rows]
