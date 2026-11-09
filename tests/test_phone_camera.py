import cv2
import numpy as np
import pytest
from starlette.websockets import WebSocketDisconnect

from backend.services.phone_camera import PAIRING_TTL_SECONDS, phone_pairings


def test_phone_pair_requires_https(client, auth, monkeypatch):
    monkeypatch.delenv("EGGSCAN_PHONE_BASE_URL", raising=False)
    response = client.post("/api/camera/phone/pair", headers=auth)
    assert response.status_code == 400
    assert "trusted HTTPS" in response.json()["detail"]


@pytest.mark.parametrize("base_url", [
    "https://localhost:8000",
    "https://127.0.0.1:8000",
    "http://192.168.1.20:8000",
    "https://192.168.1.20:8000/other-path",
    "https://8.8.8.8:8000",
])
def test_phone_pair_rejects_unusable_base_url(client, auth, monkeypatch, base_url):
    monkeypatch.setenv("EGGSCAN_PHONE_BASE_URL", base_url)
    response = client.post("https://testserver/api/camera/phone/pair", headers={**auth, "Origin": "https://testserver"})
    assert response.status_code == 400
    assert "EGGSCAN_PHONE_BASE_URL" in response.json()["detail"]


def test_phone_pair_creates_private_qr_and_one_time_webrtc_signaling(client, auth, monkeypatch):
    page = client.get("/phone-camera")
    assert page.status_code == 200
    assert "Start Sharing" in page.text
    assert "/static/js/phone-camera.js" in page.text
    dashboard = client.get("/")
    assert 'value="phone">Phone camera</option>' in dashboard.text
    monkeypatch.setenv("EGGSCAN_PHONE_BASE_URL", "https://192.168.1.20:8000")
    monkeypatch.setenv("EGGSCAN_STUN_URLS", "stun:stun.example.net:3478")
    monkeypatch.delenv("EGGSCAN_TURN_URLS", raising=False)
    monkeypatch.delenv("EGGSCAN_TURN_SHARED_SECRET", raising=False)
    pair = client.post("https://testserver/api/camera/phone/pair", headers={**auth, "Origin": "https://testserver"})
    assert pair.status_code == 200, pair.text
    body = pair.json()
    assert body["url"].startswith("https://192.168.1.20:8000/phone-camera#")
    assert 0 < body["expires_in"] <= 300
    assert body["ice_servers"] == [{"urls": ["stun:stun.example.net:3478"]}]
    assert body["turn_available"] is False
    data = body["qr_data_url"]
    assert data.startswith("data:image/png;base64,")
    import base64

    png = np.frombuffer(base64.b64decode(data.split(",", 1)[1]), dtype=np.uint8)
    qr_image = cv2.imdecode(png, cv2.IMREAD_GRAYSCALE)
    decoded, points, _ = cv2.QRCodeDetector().detectAndDecode(qr_image)
    assert points is not None
    assert decoded == body["url"]

    session_id, token = body["url"].rsplit("#", 1)[1].split(".", 1)
    validation = client.post("/api/camera/phone/validate", json={"session_id": session_id, "token": token})
    assert validation.status_code == 200
    assert validation.json()["valid"] is True
    assert validation.json()["expires_in"] <= 300
    assert validation.json()["ice_servers"] == body["ice_servers"]
    ws_headers = {"origin": "http://testserver"}
    with client.websocket_connect("/api/camera/phone/signal", headers=ws_headers) as computer:
        computer.send_json({
            "type": "join",
            "role": "computer",
            "session_id": session_id,
        })
        assert computer.receive_json()["type"] == "joined"
        computer.send_json({"type": "offer", "description": {"type": "offer", "sdp": "test-sdp"}})
        with client.websocket_connect("/api/camera/phone/signal", headers=ws_headers) as phone:
            phone.send_json({"type": "join", "role": "phone", "session_id": session_id, "token": token})
            assert phone.receive_json()["type"] == "joined"
            used_validation = client.post("/api/camera/phone/validate", json={"session_id": session_id, "token": token})
            assert used_validation.status_code == 400
            assert phone.receive_json() == {
                "type": "offer",
                "description": {"type": "offer", "sdp": "test-sdp"},
            }
            phone.send_json({"type": "answer", "description": {"type": "answer", "sdp": "answer-sdp"}})
            assert computer.receive_json()["type"] == "peer_joined"
            assert computer.receive_json() == {
                "type": "answer",
                "description": {"type": "answer", "sdp": "answer-sdp"},
            }

    with client.websocket_connect("/api/camera/phone/signal", headers=ws_headers) as replay:
        replay.send_json({"type": "join", "role": "phone", "session_id": session_id, "token": token})
        with pytest.raises(WebSocketDisconnect):
            replay.receive_json()


def test_phone_pair_accepts_public_https_hostname(client, auth, monkeypatch):
    monkeypatch.setenv("EGGSCAN_PHONE_BASE_URL", "https://eggscan.example.net")
    monkeypatch.delenv("EGGSCAN_TURN_URLS", raising=False)
    response = client.post("https://testserver/api/camera/phone/pair", headers={**auth, "Origin": "https://testserver"})
    assert response.status_code == 200, response.text
    assert response.json()["url"].startswith("https://eggscan.example.net/phone-camera#")


def test_phone_pair_issues_temporary_coturn_credentials_to_both_peers(client, auth, monkeypatch):
    import base64
    import hashlib
    import hmac
    import time

    monkeypatch.setenv("EGGSCAN_PHONE_BASE_URL", "https://eggscan.example.net")
    monkeypatch.setenv("EGGSCAN_STUN_URLS", "stun:stun.example.net:3478")
    monkeypatch.setenv("EGGSCAN_TURN_URLS", "turn:turn.example.net:3478?transport=udp,turns:turn.example.net:5349?transport=tcp")
    shared_secret = "this-is-a-test-secret-with-at-least-32-characters"
    monkeypatch.setenv("EGGSCAN_TURN_SHARED_SECRET", shared_secret)
    monkeypatch.setenv("EGGSCAN_TURN_CREDENTIAL_TTL_SECONDS", "600")

    pair = client.post("https://testserver/api/camera/phone/pair", headers={**auth, "Origin": "https://testserver"})
    assert pair.status_code == 200, pair.text
    body = pair.json()
    assert body["turn_available"] is True
    assert body["ice_servers"][0] == {"urls": ["stun:stun.example.net:3478"]}
    turn = body["ice_servers"][1]
    assert turn["urls"] == [
        "turn:turn.example.net:3478?transport=udp",
        "turns:turn.example.net:5349?transport=tcp",
    ]
    expiration, identity = turn["username"].split(":", 1)
    assert identity.startswith("eggscan:")
    assert 0 < int(expiration) - int(time.time()) <= 600
    expected = base64.b64encode(
        hmac.new(shared_secret.encode(), turn["username"].encode(), hashlib.sha1).digest()
    ).decode()
    assert turn["credential"] == expected
    assert shared_secret not in pair.text

    session_id, token = body["url"].rsplit("#", 1)[1].split(".", 1)
    phone_config = client.post(
        "/api/camera/phone/validate",
        json={"session_id": session_id, "token": token},
    )
    assert phone_config.status_code == 200
    assert phone_config.json()["turn_available"] is True
    assert phone_config.json()["ice_servers"][1]["username"].endswith(identity)


def test_turn_urls_require_shared_secret(client, auth, monkeypatch):
    monkeypatch.setenv("EGGSCAN_PHONE_BASE_URL", "https://eggscan.example.net")
    monkeypatch.setenv("EGGSCAN_TURN_URLS", "turn:turn.example.net:3478")
    monkeypatch.delenv("EGGSCAN_TURN_SHARED_SECRET", raising=False)
    response = client.post("https://testserver/api/camera/phone/pair", headers={**auth, "Origin": "https://testserver"})
    assert response.status_code == 503
    assert "EGGSCAN_TURN_SHARED_SECRET" in response.json()["detail"]


def test_expired_phone_pairing_is_rejected(client, auth, monkeypatch):
    monkeypatch.setenv("EGGSCAN_PHONE_BASE_URL", "https://eggscan.example.net")
    monkeypatch.delenv("EGGSCAN_TURN_URLS", raising=False)
    pair = client.post("https://testserver/api/camera/phone/pair", headers={**auth, "Origin": "https://testserver"})
    assert pair.status_code == 200
    session_id, token = pair.json()["url"].rsplit("#", 1)[1].split(".", 1)
    phone_pairings._sessions[session_id].created_at -= PAIRING_TTL_SECONDS + 1

    response = client.post(
        "/api/camera/phone/validate",
        json={"session_id": session_id, "token": token},
    )
    assert response.status_code == 400
    assert "invalid, expired, or already used" in response.json()["detail"]
