import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///" + (tmp_path / "test.db").as_posix())
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-at-least-32-bytes")
    monkeypatch.setenv("EGGSCAN_FORCE_MOCK", "1")
    monkeypatch.setenv("EGGSCAN_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ADMIN_EMAIL", "admin@gmail.com")
    monkeypatch.setenv("ADMIN_SESSION_TTL_MINUTES", "30")
    monkeypatch.setenv("EGGSCAN_COOKIE_SECURE", "auto")
    monkeypatch.setenv("EGGSCAN_LOCAL_DEMO_LOGIN", "false")
    os.environ["EGGSCAN_FORCE_MOCK"] = "1"
    from importlib import import_module

    auth_router = import_module("backend.routers.auth")
    auth_router._login_limiter.events.clear()

    from backend.main import create_app

    application = create_app()
    with TestClient(application) as test_client:
        from backend.auth import hash_password
        from backend.db import SessionLocal
        from backend.models import User

        with SessionLocal() as db:
            admin = db.query(User).filter(User.email == "admin@gmail.com").one()
            admin.password_hash = hash_password("admin123")
            db.commit()
        csrf = test_client.get("/api/auth/csrf")
        assert csrf.status_code == 200
        test_client.headers.update({"Origin": "http://testserver", "X-CSRF-Token": csrf.json()["csrf_token"]})
        yield test_client


@pytest.fixture()
def auth(client):
    response = client.post("/api/auth/login", json={"email": "admin@gmail.com", "password": "admin123"})
    assert response.status_code == 200, response.text
    return {"Origin": "http://testserver", "X-CSRF-Token": client.cookies.get("eggscan_csrf")}
