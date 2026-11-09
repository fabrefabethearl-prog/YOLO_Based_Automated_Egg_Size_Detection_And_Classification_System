from datetime import datetime, timedelta, timezone

from backend.auth import SESSION_COOKIE_NAME, hash_session_token


def test_local_demo_login_is_password_only_and_loopback_only(client, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.main import create_app

    monkeypatch.setenv("EGGSCAN_LOCAL_DEMO_LOGIN", "true")
    app = create_app()
    credentials = {"email": "admin@gmail.com", "password": "admin123"}

    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50001)) as local_client:
        csrf = local_client.get("/api/auth/csrf").json()["csrf_token"]
        response = local_client.post(
            "/api/auth/login",
            json=credentials,
            headers={"Origin": "http://127.0.0.1", "X-CSRF-Token": csrf},
        )
        assert response.status_code == 200
        assert response.json()["email"] == "admin@gmail.com"
        assert response.cookies.get(SESSION_COOKIE_NAME)

    with TestClient(app, base_url="http://127.0.0.1", client=("192.0.2.10", 50002)) as remote_client:
        csrf = remote_client.get("/api/auth/csrf").json()["csrf_token"]
        response = remote_client.post(
            "/api/auth/login",
            json=credentials,
            headers={"Origin": "http://127.0.0.1", "X-CSRF-Token": csrf},
        )
        assert response.status_code == 403
        assert "only on this computer" in response.json()["detail"]


def _login(client, email="admin@gmail.com", password="admin123"):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_password_login_creates_hashed_cookie_session(client):
    from backend.db import SessionLocal
    from backend.models import AdminSession

    response = _login(client)
    assert response.status_code == 200
    assert response.json()["email"] == "admin@gmail.com"
    cookie = response.cookies.get(SESSION_COOKIE_NAME)
    assert cookie and "httponly" in response.headers["set-cookie"].lower()
    with SessionLocal() as db:
        session = db.get(AdminSession, hash_session_token(cookie))
        assert session is not None
        assert cookie not in session.id
    assert client.get("/api/auth/me").json()["email"] == "admin@gmail.com"


def test_wrong_password_and_non_admin_email_are_rejected_equally(client):
    bad_password = _login(client, password="wrong-password")
    bad_email = _login(client, email="other@gmail.com")
    assert bad_password.status_code == bad_email.status_code == 401
    assert bad_password.json()["detail"] == bad_email.json()["detail"] == "Incorrect email or password."


def test_login_rate_limit(client):
    from backend.routers.auth import _login_limiter

    _login_limiter.events.clear()
    for _ in range(10):
        assert _login(client, password="wrong-password").status_code == 401
    assert _login(client, password="wrong-password").status_code == 429
    _login_limiter.events.clear()


def test_logout_revokes_session(client):
    from backend.db import SessionLocal
    from backend.models import AdminSession

    response = _login(client)
    token = response.cookies.get(SESSION_COOKIE_NAME)
    assert token
    logout = client.post("/api/auth/logout", json={})
    assert logout.status_code == 200
    assert client.get("/api/auth/me").status_code == 401
    with SessionLocal() as db:
        session = db.get(AdminSession, hash_session_token(token))
        assert session and session.revoked_at is not None


def test_expired_session_is_rejected(client):
    from backend.db import SessionLocal
    from backend.models import AdminSession

    response = _login(client)
    token = response.cookies.get(SESSION_COOKIE_NAME)
    with SessionLocal() as db:
        session = db.get(AdminSession, hash_session_token(token))
        session.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
        db.commit()
    rejected = client.get("/api/auth/me")
    assert rejected.status_code == 401
    assert "max-age=0" in rejected.headers.get("set-cookie", "").lower()


def test_private_apis_and_registration_stay_protected(client):
    assert client.get("/api/stats/overview").status_code == 401
    assert client.get("/api/size-classes").status_code == 401
    assert client.post("/api/auth/register", json={"email": "new@example.com"}).status_code == 404
    assert client.post("/api/auth/verify-otp", json={"code": "000000"}).status_code == 404


def test_login_requires_same_origin_and_csrf(client):
    denied = client.post(
        "/api/auth/login",
        json={"email": "admin@gmail.com", "password": "admin123"},
        headers={"Origin": "http://testserver", "X-CSRF-Token": ""},
    )
    assert denied.status_code == 403


def test_session_cookie_is_secure_on_https(client, monkeypatch):
    monkeypatch.setenv("EGGSCAN_COOKIE_SECURE", "true")
    started = client.get("https://testserver/api/auth/csrf", headers={"Origin": "https://testserver"})
    assert started.status_code == 200
    token = started.json()["csrf_token"]
    client.cookies.set("eggscan_csrf", token)
    response = client.post(
        "https://testserver/api/auth/login",
        json={"email": "admin@gmail.com", "password": "admin123"},
        headers={"Origin": "https://testserver", "X-CSRF-Token": token},
    )
    assert response.status_code == 200
    cookie = response.headers.get("set-cookie", "").lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie


def test_session_cookie_for_unauthenticated_bootstrap_is_cleared(client):
    client.cookies.set(SESSION_COOKIE_NAME, "stale-session-cookie")
    response = client.get("/api/auth/me")
    assert response.status_code == 401
    cookie = response.headers.get("set-cookie", "").lower()
    assert SESSION_COOKIE_NAME in cookie and "max-age=0" in cookie


def test_repeat_initialization_preserves_users_and_has_no_otp_session_marker(client):
    from backend.db import SessionLocal, engine, init_db
    from backend.models import User
    from sqlalchemy import inspect

    with SessionLocal() as db:
        user_count = db.query(User).count()
    init_db()
    init_db()
    with SessionLocal() as db:
        assert db.query(User).count() == user_count
    assert "otp_verified_at" not in {column["name"] for column in inspect(engine).get_columns("admin_sessions")}
