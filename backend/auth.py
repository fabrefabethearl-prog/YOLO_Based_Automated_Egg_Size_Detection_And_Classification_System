"""Opaque administrator sessions stored as hashes and enforced server-side."""

from __future__ import annotations

import bcrypt
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from backend.db import get_db
from backend.models import AdminSession, User

SESSION_COOKIE_NAME = "eggscan_session"
CSRF_COOKIE_NAME = "eggscan_csrf"
CSRF_HEADER_NAME = "x-csrf-token"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def session_minutes() -> int:
    try:
        return max(int(os.getenv("ADMIN_SESSION_TTL_MINUTES", os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "720"))), 1)
    except ValueError:
        return 720


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def cookie_is_secure(request: Request) -> bool:
    setting = os.getenv("EGGSCAN_COOKIE_SECURE", "auto").strip().lower()
    if setting in {"1", "true", "yes", "on"}:
        return True
    if setting in {"0", "false", "no", "off"}:
        return False
    return request.url.scheme == "https"


def set_session_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=session_minutes() * 60,
        httponly=True,
        secure=cookie_is_secure(request),
        samesite="strict",
        path="/",
    )


def clear_session_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        SESSION_COOKIE_NAME,
        path="/",
        secure=cookie_is_secure(request),
        httponly=True,
        samesite="strict",
    )


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    session_hash = hash_session_token(token)
    session = db.query(AdminSession).filter(
        AdminSession.id == session_hash,
        AdminSession.revoked_at.is_(None),
    ).one_or_none()
    if session is None:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    user = db.get(User, session.user_id)
    configured_admin = os.getenv("ADMIN_EMAIL", "").strip().lower()
    if user is None or not configured_admin or not hmac.compare_digest(user.email.strip().lower(), configured_admin):
        raise HTTPException(status_code=401, detail="Not authenticated.")
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Session expired. Please sign in again.")
    request.state.admin_session_id = session.id
    return user
