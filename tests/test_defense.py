"""Defense-ready scan traceability, honesty checks, and calibration."""

import csv
import io

import cv2
import numpy as np


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


def _scan(client, auth, *, actual_class=None, session_id=None):
    data = {}
    if session_id:
        data["session_id"] = session_id
    response = client.post(
        "/api/scan",
        files={"file": ("egg.jpg", ellipse_jpeg(), "image/jpeg")},
        data=data,
        headers=auth,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    if actual_class and body.get("id"):
        patched = client.patch(
            f"/api/scans/{body['id']}",
            json={"actual_class": actual_class},
            headers=auth,
        )
        assert patched.status_code == 200, patched.text
        body.update(patched.json())
    return body


def test_scan_records_operator_model_and_steps(client, auth):
    body = _scan(client, auth, actual_class="Large", session_id="lab-1")
    assert body["saved"] is True
    assert body["operator_email"] == "admin@gmail.com"
    assert body["model_version"] == "mock"
    assert body["session_id"] == "lab-1"
    assert [step["status"] for step in body["steps"]] == ["done"] * 6
    assert body["steps"][2]["id"] == "detect"
    assert body["steps"][2]["ms"] == body["inference_ms"]

    listed = client.get("/api/scans?q=" + str(body["id"]), headers=auth)
    item = listed.json()["items"][0]
    assert item["operator_email"] == "admin@gmail.com"
    assert item["model_version"] == "mock"


def test_low_confidence_is_not_saved_and_marks_the_detect_step(client, auth):
    response = client.post(
        "/api/scan",
        files={"file": ("blank.jpg", blank_jpeg(), "image/jpeg")},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["saved"] is False
    failed = [step for step in body["steps"] if step["status"] == "fail"]
    assert failed[0]["id"] == "detect"
    assert failed[0]["reason"]
    logs = client.get("/api/logs?level=WARN", headers=auth)
    assert logs.status_code == 200
    assert logs.json()["total"] >= 1


def test_patch_recomputes_class_and_filters(client, auth):
    saved = _scan(client, auth)
    patched = client.patch(
        f"/api/scans/{saved['id']}",
        json={"actual_class": "Large"},
        headers=auth,
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["actual_class"] == "Large"
    assert body["correct"] is (body["predicted_class"] == "Large")

    weighed = client.get("/api/scans?actual_name=Large", headers=auth)
    assert weighed.json()["total"] == 1
    pending = client.get("/api/scans?correctness=unlabeled", headers=auth)
    assert pending.json()["total"] == 0
    today = body["created_at"][:10]
    assert client.get(f"/api/scans?date_from={today}&date_to={today}", headers=auth).json()["total"] == 1
    assert client.get("/api/scans?date_from=1999-01-01&date_to=1999-01-02", headers=auth).json()["total"] == 0

    overview = client.get("/api/stats/overview", headers=auth).json()
    assert overview["verified_count"] == 1
    assert overview["unverified_count"] == 0
    assert overview["last_scan_at"]
    assert overview["model"]["mode"] == "MOCK"


def test_csv_includes_trace_columns(client, auth):
    _scan(client, auth, session_id="lab-1")
    response = client.get("/api/scans/export", headers=auth)
    assert response.status_code == 200
    rows = list(csv.reader(io.StringIO(response.text)))
    assert "operator_email" in rows[0]
    assert "model_version" in rows[0]
    assert "session_id" in rows[0]
    assert "area_mm2" in rows[0]
    assert "inference_ms" in rows[0]
    assert "admin@gmail.com" in rows[1]
    assert "lab-1" in rows[1]
    assert "eggscan-records.csv" in response.headers["content-disposition"]


def test_report_csv_has_professional_headers_and_handles_empty_dataset(client, auth):
    empty = client.get("/api/scans/export?report=true", headers=auth)
    assert empty.status_code == 200
    empty_rows = list(csv.reader(io.StringIO(empty.text)))
    assert len(empty_rows) == 1
    assert "EggScan_Report_" in empty.headers["content-disposition"]

    saved = _scan(client, auth)
    patched = client.patch(
        f"/api/scans/{saved['id']}",
        json={"actual_class": "Medium"},
        headers=auth,
    )
    assert patched.status_code == 200
    exported = client.get("/api/scans/export?report=true", headers=auth)
    rows = list(csv.reader(io.StringIO(exported.text)))
    assert rows[0] == [
        "Record ID",
        "Scan Date",
        "Scan Time (UTC)",
        "Predicted Egg Size Class",
        "Actual Egg Size Class",
        "Egg Length (mm)",
        "Egg Width (mm)",
        "Measured Area (mm²)",
        "Detection Confidence",
        "Classification Result",
        "Operator",
        "Model Identifier",
        "Scan Session ID",
    ]
    assert rows[1][1]
    assert rows[1][2]
    assert rows[1][10] == "admin@gmail.com"
    assert rows[1][9] in {"Correct", "Mismatch"}


def test_analytics_report_uses_only_real_supported_verified_records(client, auth):
    from backend.db import SessionLocal
    from backend.models import Scan

    mock_verified = _scan(client, auth, actual_class="Small")
    correct = _scan(client, auth, actual_class="Small")
    incorrect = _scan(client, auth, actual_class="Jumbo")
    unverified = _scan(client, auth)
    unsupported = _scan(client, auth, actual_class="Small")

    db = SessionLocal()
    try:
        for scan_id in (correct["id"], incorrect["id"], unverified["id"], unsupported["id"]):
            db.get(Scan, scan_id).is_mock = False
        db.get(Scan, incorrect["id"]).predicted_class = "Large"
        db.get(Scan, unsupported["id"]).predicted_class = "Not a supported class"
        db.commit()
    finally:
        db.close()

    response = client.get("/api/stats/report", headers=auth)
    assert response.status_code == 200, response.text
    report = response.json()
    summary = report["summary"]
    evaluation = report["evaluation"]
    assert summary["total_scans"] == 5
    assert summary["verified_count"] == 2
    assert summary["correct_count"] == 1
    assert summary["incorrect_count"] == 1
    assert summary["accuracy"] == 0.5
    assert evaluation["accuracy"] == 0.5
    assert sum(map(sum, evaluation["confusion"]["matrix"])) == 2
    assert report["quality"]["excluded_mock_scans"] == 1
    assert report["quality"]["excluded_unverified_scans"] == 1
    assert report["quality"]["excluded_invalid_label_scans"] == 1
    assert sum(row["count"] for row in evaluation["actual_distribution"]) == 2
    assert report["prediction_distribution"]
    assert sum(row["support"] for row in evaluation["per_class"]) == 2



def test_reclassify_keeps_the_scan(client, auth):
    saved = _scan(client, auth, actual_class="Small")
    response = client.post(f"/api/scans/{saved['id']}/reclassify", headers=auth)
    assert response.status_code == 200, response.text
    assert response.json()["predicted_class"]
    assert response.json()["actual_class"] == "Small"


def test_model_sample_note_and_balance(client, auth, tmp_path, monkeypatch):
    from backend.services import model_info
    from backend.db import SessionLocal
    from backend.models import Scan
    monkeypatch.setattr(model_info, "ML_RUNS", tmp_path / "empty-runs")
    scan = _scan(client, auth, actual_class="Large")
    db = SessionLocal()
    try:
        db.get(Scan, scan["id"]).is_mock = False
        db.commit()
    finally:
        db.close()
    body = client.get("/api/stats/model", headers=auth).json()
    assert body["sample_size"] == 1
    assert body["small_sample"] is True
    assert body["small_sample_note"] == "Small sample: results are indicative only."
    large = next(row for row in body["class_balance"] if row["name"] == "Large")
    assert large["count"] == 1
    assert large["low"] is True
    assert body["training"] is None
    assert body["model"]["version"] == "mock"
    empty = client.get("/api/stats/model", headers=auth)
    assert empty.status_code == 200


def test_conclusion_waits_for_three_trials(client, auth):
    def add(eggs=4):
        return client.post(
            "/api/benchmark",
            json={
                "eggs": eggs,
                "manual_time_s": 40,
                "eggscan_time_s": 20,
                "manual_correct": 3,
                "eggscan_correct": 4,
            },
            headers=auth,
        )

    add()
    add()
    early = client.get("/api/benchmark", headers=auth).json()["conclusion"]
    assert early["ready"] is False
    assert "at least 3 trials" in early["text"]
    add()
    ready = client.get("/api/benchmark", headers=auth).json()["conclusion"]
    assert ready["ready"] is True
    assert ready["text"].startswith("Across 3 trials of 12 eggs")
    assert "faster per egg" in ready["text"]
    assert "percentage points" in ready["text"]
    assert "SD" in ready["text"]


def test_trial_can_use_a_scan_session(client, auth):
    saved = _scan(client, auth, actual_class="Large", session_id="bench-a")
    response = client.post(
        "/api/benchmark",
        json={
            "eggs": 1,
            "manual_time_s": 12,
            "eggscan_time_s": 0,
            "manual_correct": 1,
            "eggscan_correct": 0,
            "session_id": "bench-a",
        },
        headers=auth,
    )
    assert response.status_code == 200, response.text
    trial = response.json()
    assert trial["session_id"] == "bench-a"
    assert trial["eggs"] == 1
    expected_correct = 1 if saved["predicted_class"] == saved["actual_class"] else 0
    assert trial["eggscan_correct"] == expected_correct
    assert trial["eggscan_time_s"] == round(saved["inference_ms"] / 1000, 3)
    missing = client.post(
        "/api/benchmark",
        json={
            "eggs": 1,
            "manual_time_s": 12,
            "eggscan_time_s": 1,
            "manual_correct": 1,
            "eggscan_correct": 1,
            "session_id": "missing-session",
        },
        headers=auth,
    )
    assert missing.status_code == 400


def test_camera_calibration_reports_previous_and_new(client, auth):
    measured = client.post(
        "/api/settings/measure-reference",
        files={"file": ("ref.jpg", ellipse_jpeg(), "image/jpeg")},
        headers=auth,
    )
    assert measured.status_code == 200, measured.text
    pixels = measured.json()["pixel_length"]
    assert pixels > 50

    saved = client.post(
        "/api/settings/calibrate-camera",
        json={"known_mm": 40, "pixel_length": 100},
        headers=auth,
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["previous_pixels_per_mm"] == 5
    assert body["pixels_per_mm"] == 2.5
    assert client.get("/api/settings", headers=auth).json()["pixels_per_mm"] == 2.5
