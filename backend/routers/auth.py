"""Administrator password authentication."""

from __future__ import annotations

import ipaddress
import hmac
import hashlib
import os
import secrets
import smtplib
import ssl
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.auth import (
    CSRF_COOKIE_NAME,
    clear_session_cookie,
    cookie_is_secure,
    get_current_user,
    hash_session_token,
    new_session_token,
    session_minutes,
    set_session_cookie,
    verify_password,
)
from backend.db import get_db
from backend.models import AdminOTPChallenge, AdminSession, User
from backend.schemas import LoginRequest, OTPEmailRequest, OTPVerifyRequest
from backend.services.audit import add_log
from backend.services.detector import detector

router = APIRouter(prefix="/api/auth", tags=["auth"])


class _RateLimiter:
    """Per-process throttling for password sign-in attempts."""

    def __init__(self, limit: int, window_seconds: int = 15 * 60):
        self.limit = limit
        self.window_seconds = window_seconds
        self.events: dict[str, deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            queue = self.events[key]
            while queue and now - queue[0] >= self.window_seconds:
                queue.popleft()
            if len(queue) >= self.limit:
                return False
            queue.append(now)
            return True


_login_limiter = _RateLimiter(limit=10)
_otp_verify_limiter = _RateLimiter(limit=20)
_otp_resend_limiter = _RateLimiter(limit=10)
OTP_COOKIE_NAME = "eggscan_challenge"
OTP_TTL_SECONDS = 5 * 60
OTP_RESEND_SECONDS = 60
OTP_MAX_ATTEMPTS = 5


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _client_key(request: Request, action: str) -> str:
    remote = request.client.host if request.client else "unknown-client"
    return f"{action}:{remote}"


def _otp_code_hash(email: str, code: str) -> str:
    secret = os.getenv("SECRET_KEY", "").strip()
    if len(secret) < 32:
        raise RuntimeError("SECRET_KEY must contain at least 32 characters for email verification.")
    return hmac.new(secret.encode(), f"{email}:{code}".encode(), hashlib.sha256).hexdigest()


def _challenge_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _is_loopback_request(request: Request) -> bool:
    if request.client is None or request.url.hostname is None:
        return False
    try:
        client_is_loopback = ipaddress.ip_address(request.client.host).is_loopback
        hostname = request.url.hostname.casefold()
        host_is_loopback = hostname == "localhost" or ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False
    return client_is_loopback and host_is_loopback


def _local_demo_login_enabled(request: Request) -> bool:
    enabled = os.getenv("EGGSCAN_LOCAL_DEMO_LOGIN", "").strip().lower() in {"1", "true", "yes", "on"}
    if enabled and not _is_loopback_request(request):
        raise HTTPException(status_code=403, detail="Password-only demo sign-in is available only on this computer.")
    return enabled


def _create_session(user: User, request: Request, response: Response, db: Session, method: str):
    now = _now()
    session_token = new_session_token()
    db.add(AdminSession(
        id=hash_session_token(session_token), user_id=user.id, created_at=now,
        expires_at=now + timedelta(minutes=session_minutes()),
    ))
    add_log(db, "INFO", f"{user.email} signed in with password and {method}.")
    db.commit()
    _clear_challenge_cookie(response, request)
    set_session_cookie(response, request, session_token)
    response.headers["Cache-Control"] = "no-store"
    return {"email": user.email, "mock_mode": detector.mock}


def _send_otp(email: str, code: str) -> None:
    host = os.getenv("SMTP_HOST", "smtp.gmail.com").strip()
    port = int(os.getenv("SMTP_PORT", "587"))
    sender = os.getenv("SMTP_EMAIL", "").strip()
    password = os.getenv("SMTP_APP_PASSWORD", "").replace(" ", "")
    if not sender or not password:
        raise RuntimeError("Gmail SMTP credentials are not configured.")
    message = EmailMessage()
    message["Subject"] = "Your EggScan sign-in code"
    message["From"] = sender
    message["To"] = email
    message.set_content(f"Your EggScan administrator verification code is {code}. It expires in 5 minutes. If you did not request this code, you can ignore this email.")
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=15, context=ssl.create_default_context()) as server:
            server.login(sender, password)
            server.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.ehlo()
            server.starttls(context=ssl.create_default_context())
            server.ehlo()
            server.login(sender, password)
            server.send_message(message)


def _set_challenge_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        OTP_COOKIE_NAME, token, max_age=OTP_TTL_SECONDS, httponly=True,
        secure=cookie_is_secure(request), samesite="strict", path="/api/auth",
    )
    response.headers["Cache-Control"] = "no-store"


def _clear_challenge_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        OTP_COOKIE_NAME, path="/api/auth", secure=cookie_is_secure(request),
        httponly=True, samesite="strict",
    )


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    if not _login_limiter.allow(_client_key(request, "login")):
        raise HTTPException(status_code=429, detail="Too many sign-in attempts. Wait 15 minutes before trying again.")

    configured_admin = os.getenv("ADMIN_EMAIL", "").strip().lower()
    user = db.query(User).filter(func.lower(User.email) == email).one_or_none()
    is_authorized_email = bool(configured_admin) and hmac.compare_digest(email, configured_admin)
    if not is_authorized_email or user is None or not verify_password(body.password, user.password_hash):
        add_log(db, "WARN", f"Failed login for {email}.")
        db.commit()
        raise HTTPException(status_code=401, detail="Incorrect email or password.")

    if _local_demo_login_enabled(request):
        return _create_session(user, request, response, db, "local demo mode")

    now = _now()
    existing = db.get(AdminOTPChallenge, user.email.lower())
    if existing and existing.created_at > now - timedelta(seconds=OTP_RESEND_SECONDS):
        prior_token = request.cookies.get(OTP_COOKIE_NAME, "")
        if prior_token and hmac.compare_digest(existing.challenge_token_hash, _challenge_token_hash(prior_token)) and existing.expires_at > now:
            _set_challenge_cookie(response, request, prior_token)
            return {"otp_required": True, "email": user.email, "expires_in": int((existing.expires_at - now).total_seconds()), "resend_after": OTP_RESEND_SECONDS - int((now - existing.created_at).total_seconds())}
        raise HTTPException(status_code=429, detail="A verification code was just sent. Wait a minute before requesting another.")
    code = f"{secrets.randbelow(1_000_000):06d}"
    challenge_token = secrets.token_urlsafe(32)
    challenge = existing or AdminOTPChallenge(email=user.email.lower())
    challenge.code_hash = _otp_code_hash(user.email.lower(), code)
    challenge.challenge_token_hash = _challenge_token_hash(challenge_token)
    challenge.created_at = now
    challenge.expires_at = now + timedelta(seconds=OTP_TTL_SECONDS)
    challenge.attempts = 0
    db.add(challenge)
    db.commit()
    try:
        _send_otp(user.email, code)
    except Exception:
        db.delete(challenge)
        db.commit()
        raise HTTPException(status_code=503, detail="Could not send the verification email. Check the Gmail SMTP settings and try again.")
    add_log(db, "INFO", f"Verification code requested for {user.email}.")
    db.commit()
    _set_challenge_cookie(response, request, challenge_token)
    return {"otp_required": True, "email": user.email, "expires_in": OTP_TTL_SECONDS, "resend_after": OTP_RESEND_SECONDS}


@router.post("/resend-otp")
def resend_otp(body: OTPEmailRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    if not _otp_resend_limiter.allow(_client_key(request, "otp-resend")):
        raise HTTPException(status_code=429, detail="Too many code requests. Try again later.")
    email = body.email.lower()
    challenge_token = request.cookies.get(OTP_COOKIE_NAME, "")
    challenge = db.get(AdminOTPChallenge, email)
    if not challenge or not challenge_token or not hmac.compare_digest(challenge.challenge_token_hash, _challenge_token_hash(challenge_token)):
        raise HTTPException(status_code=401, detail="Sign-in verification expired. Please enter your password again.")
    now = _now()
    if challenge.expires_at <= now:
        db.delete(challenge)
        db.commit()
        raise HTTPException(status_code=401, detail="Sign-in verification expired. Please enter your password again.")
    remaining = OTP_RESEND_SECONDS - int((now - challenge.created_at).total_seconds())
    if remaining > 0:
        raise HTTPException(status_code=429, detail=f"Wait {remaining} seconds before requesting another code.")
    code = f"{secrets.randbelow(1_000_000):06d}"
    challenge.code_hash = _otp_code_hash(email, code)
    challenge.created_at = now
    challenge.expires_at = now + timedelta(seconds=OTP_TTL_SECONDS)
    challenge.attempts = 0
    db.commit()
    try:
        _send_otp(email, code)
    except Exception:
        db.delete(challenge)
        db.commit()
        _clear_challenge_cookie(response, request)
        raise HTTPException(status_code=503, detail="Could not send the verification email. Check the Gmail SMTP settings and try again.")
    _set_challenge_cookie(response, request, challenge_token)
    return {"otp_required": True, "expires_in": OTP_TTL_SECONDS, "resend_after": OTP_RESEND_SECONDS}


@router.post("/verify-otp")
def verify_otp(body: OTPVerifyRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    if not _otp_verify_limiter.allow(_client_key(request, "otp-verify")):
        raise HTTPException(status_code=429, detail="Too many verification attempts. Try again later.")
    email = body.email.lower()
    token = request.cookies.get(OTP_COOKIE_NAME, "")
    challenge = db.get(AdminOTPChallenge, email)
    if not challenge or not token or not hmac.compare_digest(challenge.challenge_token_hash, _challenge_token_hash(token)):
        raise HTTPException(status_code=401, detail="Code is invalid or expired. Sign in again to request a new one.")
    now = _now()
    if challenge.expires_at <= now or challenge.attempts >= OTP_MAX_ATTEMPTS:
        db.delete(challenge)
        db.commit()
        _clear_challenge_cookie(response, request)
        raise HTTPException(status_code=401, detail="Code is invalid or expired. Sign in again to request a new one.")
    expected = _otp_code_hash(email, body.code)
    if not hmac.compare_digest(expected, challenge.code_hash):
        challenge.attempts += 1
        if challenge.attempts >= OTP_MAX_ATTEMPTS:
            db.delete(challenge)
            db.commit()
            _clear_challenge_cookie(response, request)
            raise HTTPException(status_code=401, detail="Too many incorrect codes. Sign in again to request a new one.")
        db.commit()
        raise HTTPException(status_code=401, detail="Incorrect verification code.")
    user = db.query(User).filter(func.lower(User.email) == email).one_or_none()
    configured_admin = os.getenv("ADMIN_EMAIL", "").strip().lower()
    if user is None or not configured_admin or not hmac.compare_digest(email, configured_admin):
        db.delete(challenge)
        db.commit()
        _clear_challenge_cookie(response, request)
        raise HTTPException(status_code=401, detail="Code is invalid or expired. Sign in again to request a new one.")
    db.delete(challenge)
    return _create_session(user, request, response, db, "email verification")


@router.post("/logout")
def logout(request: Request, response: Response, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    session_id = getattr(request.state, "admin_session_id", None)
    if session_id:
        session = db.query(AdminSession).filter(AdminSession.id == session_id, AdminSession.revoked_at.is_(None)).one_or_none()
        if session is not None:
            session.revoked_at = _now()
    add_log(db, "INFO", f"Authentication: {user.email} signed out successfully.")
    db.commit()
    clear_session_cookie(response, request)
    _clear_challenge_cookie(response, request)
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {"email": user.email, "mock_mode": detector.mock}


@router.get("/csrf")
def csrf(request: Request, response: Response):
    token = request.cookies.get(CSRF_COOKIE_NAME, "")
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
    if len(token) < 32 or any(character not in allowed for character in token):
        token = secrets.token_urlsafe(32)
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
        max_age=session_minutes() * 60,
        httponly=False,
        secure=cookie_is_secure(request),
        samesite="strict",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"csrf_token": token}
