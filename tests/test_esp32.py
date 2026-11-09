"""ESP32 address is a local IP, and the last settings save can be undone."""


def test_esp_address_must_be_a_local_ip(client, auth):
    response = client.post("/api/camera/esp", json={"ip": "8.8.8.8"}, headers=auth)
    assert response.status_code == 400
    assert "local" in response.json()["detail"].lower()


def test_esp_address_is_saved_and_read_back(client, auth, monkeypatch):
    monkeypatch.setattr("backend.routers.camera.capture", lambda address: b"\xff\xd8" + b"0" * 120)
    saved = client.post("/api/camera/esp", json={"ip": "http://192.168.254.20/capture"}, headers=auth)
    assert saved.status_code == 200, saved.text
    assert saved.json()["ip"] == "192.168.254.20"
    listed = client.get("/api/camera/esp", headers=auth)
    assert listed.json()["ip"] == "192.168.254.20"


def test_undo_restores_the_previous_settings(client, auth):
    before = client.get("/api/settings", headers=auth).json()
    assert before["can_undo"] is False
    changed = client.put(
        "/api/settings",
        json={
            "confidence": 0.42,
            "pixels_per_mm": before["pixels_per_mm"],
            "classification_mode": before["classification_mode"],
            "camera_index": before["camera_index"],
            "out_of_range": before["out_of_range"],
        },
        headers=auth,
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["confidence"] == 0.42
    assert changed.json()["can_undo"] is True
    restored = client.post("/api/settings/undo", headers=auth)
    assert restored.status_code == 200, restored.text
    assert restored.json()["confidence"] == before["confidence"]
    assert restored.json()["can_undo"] is False
    again = client.post("/api/settings/undo", headers=auth)
    assert again.status_code == 400
