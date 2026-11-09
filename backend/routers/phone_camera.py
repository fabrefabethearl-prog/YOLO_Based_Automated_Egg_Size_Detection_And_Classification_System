"""Authenticated pairing and local WebRTC signaling for phone cameras."""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

import cv2
from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.auth import SESSION_COOKIE_NAME, get_current_user, hash_session_token
from backend import db as database
from backend.models import AdminSession, User
from backend.services.phone_camera import PAIRING_TTL_SECONDS, PhonePairing, phone_pairings

router = APIRouter(tags=["phone camera"])


class PhonePairValidation(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)
    token: str = Field(min_length=1, max_length=100)


def _phone_base_url(request: Request) -> str:
    if request.url.scheme != "https":
        raise HTTPException(
            status_code=400,
            detail="Phone camera pairing requires opening EggScan over trusted HTTPS. Follow the Phone Camera setup in README.md.",
        )
    configured = os.getenv("EGGSCAN_PHONE_BASE_URL", "").strip().rstrip("/")
    try:
        parsed = urlsplit(configured)
        host = parsed.hostname or ""
        _ = parsed.port
    except ValueError:
        parsed = None
        host = ""
    valid_host = False
    if host:
        try:
            address = ipaddress.ip_address(host)
            valid_host = address.version == 4 and address.is_private and not address.is_loopback and not address.is_unspecified
        except ValueError:
            try:
                ascii_host = host.encode("idna").decode("ascii").rstrip(".")
                labels = ascii_host.split(".")
                valid_host = (
                    len(labels) >= 2
                    and ascii_host.lower() != "localhost"
                    and not ascii_host.lower().endswith((".localhost", ".local"))
                    and all(
                        len(label) <= 63
                        and re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?", label)
                        for label in labels
                    )
                )
            except UnicodeError:
                valid_host = False
    if (
        not configured
        or parsed is None
        or parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
        or not valid_host
    ):
        raise HTTPException(
            status_code=400,
            detail="Set EGGSCAN_PHONE_BASE_URL to a trusted HTTPS public hostname or private LAN IPv4 address, including its port when needed. Do not use localhost, loopback, public IP addresses, or a URL path.",
        )
    return configured


def _ice_server_urls(name: str, allowed_schemes: set[str], default: str = "") -> list[str]:
    values = [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]
    for value in values:
        scheme, separator, endpoint = value.partition(":")
        if (
            not separator
            or scheme.lower() not in allowed_schemes
            or not endpoint
            or any(char.isspace() for char in value)
        ):
            raise HTTPException(status_code=503, detail=f"{name} contains an invalid ICE server URL.")
    return values


def _phone_ice_configuration(session_id: str) -> dict:
    stun_urls = _ice_server_urls(
        "EGGSCAN_STUN_URLS",
        {"stun", "stuns"},
        "stun:stun.l.google.com:19302",
    )
    turn_urls = _ice_server_urls("EGGSCAN_TURN_URLS", {"turn", "turns"})
    servers = [{"urls": stun_urls}] if stun_urls else []
    if turn_urls:
        secret = os.getenv("EGGSCAN_TURN_SHARED_SECRET", "")
        if len(secret) < 32:
            raise HTTPException(
                status_code=503,
                detail="TURN URLs are configured, but EGGSCAN_TURN_SHARED_SECRET must contain at least 32 characters.",
            )
        try:
            ttl = int(os.getenv("EGGSCAN_TURN_CREDENTIAL_TTL_SECONDS", "3600"))
        except ValueError as exc:
            raise HTTPException(status_code=503, detail="TURN credential lifetime must be an integer.") from exc
        if not 300 <= ttl <= 86400:
            raise HTTPException(status_code=503, detail="TURN credential lifetime must be between 300 and 86400 seconds.")
        username = f"{int(time.time()) + ttl}:eggscan:{session_id}"
        credential = base64.b64encode(
            hmac.new(secret.encode("utf-8"), username.encode("utf-8"), hashlib.sha1).digest()
        ).decode("ascii")
        servers.append({
            "urls": turn_urls,
            "username": username,
            "credential": credential,
            "credentialType": "password",
        })
    return {"ice_servers": servers, "turn_available": bool(turn_urls)}


def _qr_data_url(value: str) -> str:
    try:
        matrix = cv2.QRCodeEncoder_create().encode(value)
        matrix = cv2.copyMakeBorder(matrix, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=255)
        image = cv2.resize(matrix, (320, 320), interpolation=cv2.INTER_NEAREST)
        ok, encoded = cv2.imencode(".png", image)
        if not ok:
            raise ValueError("QR encoding failed")
        return "data:image/png;base64," + base64.b64encode(encoded.tobytes()).decode("ascii")
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Could not generate the phone pairing QR code.") from exc


@router.post("/api/camera/phone/pair")
async def create_phone_pair(
    request: Request,
    user: User = Depends(get_current_user),
):
    base_url = _phone_base_url(request)
    try:
        session = await phone_pairings.create(user.email)
    except ValueError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    url = f"{base_url}/phone-camera#{session.session_id}.{session.token}"
    qr_data_url = _qr_data_url(url)
    ice = _phone_ice_configuration(session.session_id)
    return {
        "session_id": session.session_id,
        "url": url,
        "qr_data_url": qr_data_url,
        "expires_in": max(0, int(PAIRING_TTL_SECONDS - (time.monotonic() - session.created_at))),
        **ice,
    }


@router.post("/api/camera/phone/validate")
async def validate_phone_pair(pair: PhonePairValidation):
    expires_in = await phone_pairings.validate_phone(pair.session_id, pair.token)
    if expires_in is None:
        raise HTTPException(status_code=400, detail="This phone pairing code is invalid, expired, or already used. Generate a new QR code on the computer.")
    return {"valid": True, "expires_in": expires_in, **_phone_ice_configuration(pair.session_id)}


def _desktop_owner(session_token: str) -> str | None:
    if not session_token or database.SessionLocal is None:
        return None
    db: Session = database.SessionLocal()
    try:
        session_id = hash_session_token(session_token)
        session = db.query(AdminSession).filter(
            AdminSession.id == session_id,
            AdminSession.revoked_at.is_(None),
        ).one_or_none()
        if session is None:
            return None
        user = db.query(User).filter(User.id == session.user_id).one_or_none()
        admin_email = os.getenv("ADMIN_EMAIL", "").strip().lower()
        if user is None or not admin_email or not hmac.compare_digest(user.email.strip().lower(), admin_email):
            return None
        expires_at = session.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return user.email if expires_at > datetime.now(timezone.utc) else None
    finally:
        db.close()


@router.websocket("/api/camera/phone/signal")
async def phone_signal(websocket: WebSocket):
    origin = websocket.headers.get("origin", "")
    try:
        parsed_origin = urlsplit(origin)
    except ValueError:
        parsed_origin = None
    expected_scheme = {"ws": "http", "wss": "https"}.get(websocket.url.scheme)
    if (
        parsed_origin is None
        or parsed_origin.scheme != expected_scheme
        or parsed_origin.netloc.casefold() != websocket.headers.get("host", "").casefold()
    ):
        await websocket.close(code=1008, reason="Invalid origin")
        return
    await websocket.accept()
    session: PhonePairing | None = None
    role = ""
    try:
        first = await websocket.receive_json()
        if not isinstance(first, dict) or first.get("type") != "join":
            await websocket.close(code=1008, reason="Invalid pairing request")
            return
        role = first.get("role", "")
        session_id = str(first.get("session_id", ""))
        credential = str(first.get("token", "")) if role == "phone" else websocket.cookies.get(SESSION_COOKIE_NAME, "")
        if role == "computer":
            owner = _desktop_owner(credential)
            if owner is None:
                await websocket.close(code=1008, reason="Computer login expired")
                return
            credential = owner
        session = await phone_pairings.join(session_id, role, credential, websocket)
        if session is None:
            await websocket.close(code=1008, reason="Pairing code is invalid, expired, or already used")
            return
        await websocket.send_json({"type": "joined", "role": role})
        for message in await phone_pairings.pending_messages(session, role):
            await websocket.send_json(message)
        peer = session.peers.get("phone" if role == "computer" else "computer")
        if peer:
            await peer.send_json({"type": "peer_joined", "role": role})
        while True:
            raw = await websocket.receive_text()
            if len(raw) > 24_576:
                await websocket.close(code=1009, reason="Signaling message is too large")
                return
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.close(code=1008, reason="Invalid signaling message")
                return
            if not isinstance(message, dict) or message.get("type") not in {"offer", "answer", "candidate", "disconnect"}:
                await websocket.close(code=1008, reason="Unsupported signaling message")
                return
            if message["type"] == "disconnect":
                return
            await phone_pairings.relay(session, role, message)
    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.close(code=1011, reason="Phone camera signaling ended")
        except Exception:
            pass
    finally:
        if session and role:
            peer = await phone_pairings.disconnect(session, role)
            if peer:
                try:
                    await peer.send_json({"type": "disconnect"})
                    await peer.close(code=1000, reason="Other camera peer disconnected")
                except Exception:
                    pass
