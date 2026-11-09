from datetime import datetime, timedelta, timezone

from backend import db as database
from backend.models import Log


def _insert_logs(rows):
    db = database.SessionLocal()
    try:
        db.add_all([Log(level=level, message=message, created_at=created_at) for level, message, created_at in rows])
        db.commit()
    finally:
        db.close()


def test_log_filters_can_be_combined_and_page_newest_first(client, auth):
    now = datetime.now(timezone.utc)
    _insert_logs([
        ("INFO", "alice@example.com saved live scan record #401.", now - timedelta(minutes=3)),
        ("WARN", "Authentication: failed login attempt for bob@example.com.", now - timedelta(minutes=2)),
        ("ERROR", "Scan failed: camera timeout.", now - timedelta(minutes=1)),
        ("INFO", "alice@example.com saved live scan record #402.", now),
    ])

    response = client.get(
        "/api/logs",
        params={
            "q": "saved live scan record",
            "actor": "alice@example.com",
            "category": "scan",
            "date_from": now.date().isoformat(),
            "date_to": now.date().isoformat(),
            "page": 1,
            "page_size": 1,
        },
        headers=auth,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert payload["page"] == 1
    assert payload["pages"] == 2
    assert len(payload["items"]) == 1
    assert payload["items"][0]["message"].endswith("#402.")
    assert payload["items"][0]["category"] == "scan"
    assert payload["items"][0]["actor"] == "alice@example.com"
    assert payload["level_counts"]["INFO"] == 2
    assert payload["recent_count"] >= 2


def test_log_search_includes_id_and_redacts_secrets_without_changing_stored_message(client, auth):
    _insert_logs([
        ("INFO", "Authentication: user@example.com signed in; token=demo-secret-value", datetime.now(timezone.utc)),
    ])
    db = database.SessionLocal()
    try:
        inserted = db.query(Log).filter(Log.message.contains("demo-secret-value")).one()
        log_id = inserted.id
        original = inserted.message
    finally:
        db.close()

    response = client.get("/api/logs", params={"q": str(log_id)}, headers=auth)

    assert response.status_code == 200
    row = next(item for item in response.json()["items"] if item["id"] == log_id)
    assert "demo-secret-value" not in row["message"]
    assert "token=[REDACTED]" in row["message"]
    db = database.SessionLocal()
    try:
        assert db.query(Log).filter(Log.id == log_id).one().message == original
    finally:
        db.close()


def test_logs_remain_authenticated_and_reject_invalid_filter_ranges(client, auth):
    session_cookie = client.cookies.get("eggscan_session")
    client.cookies.delete("eggscan_session")
    assert client.get("/api/logs").status_code == 401
    client.cookies.set("eggscan_session", session_cookie)
    response = client.get("/api/logs", params={"date_from": "2026-10-20", "date_to": "2026-10-19"}, headers=auth)
    assert response.status_code == 400

