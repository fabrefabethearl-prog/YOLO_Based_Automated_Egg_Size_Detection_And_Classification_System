"""Short-lived, one-phone WebRTC pairing sessions for Live Scan."""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

PAIRING_TTL_SECONDS = 300
MAX_SESSIONS = 20


@dataclass
class PhonePairing:
    session_id: str
    token: str
    owner: str
    created_at: float = field(default_factory=time.monotonic)
    phone_used: bool = False
    peers: dict[str, WebSocket] = field(default_factory=dict)
    pending: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: {"computer": [], "phone": []})


class PhonePairingStore:
    def __init__(self) -> None:
        self._sessions: dict[str, PhonePairing] = {}
        self._lock = asyncio.Lock()

    async def create(self, owner: str) -> PhonePairing:
        async with self._lock:
            self._expire()
            if len(self._sessions) >= MAX_SESSIONS:
                raise ValueError("Too many active phone pairing sessions. Stop an existing scan and try again.")
            session = PhonePairing(
                session_id=secrets.token_urlsafe(18),
                token=secrets.token_urlsafe(32),
                owner=owner,
            )
            self._sessions[session.session_id] = session
            return session

    async def join(self, session_id: str, role: str, credential: str, websocket: WebSocket) -> PhonePairing | None:
        async with self._lock:
            self._expire()
            session = self._sessions.get(session_id)
            if session is None or role not in ("computer", "phone"):
                return None
            if role == "phone":
                if (
                    session.phone_used
                    or time.monotonic() - session.created_at > PAIRING_TTL_SECONDS
                    or not secrets.compare_digest(session.token, credential)
                ):
                    return None
                session.phone_used = True
            elif role == "computer" and session.owner != credential:
                return None
            if role in session.peers:
                return None
            session.peers[role] = websocket
            return session

    async def validate_phone(self, session_id: str, credential: str) -> int | None:
        """Check a QR credential without consuming its one-use phone join."""
        async with self._lock:
            self._expire()
            session = self._sessions.get(session_id)
            if (
                session is None
                or session.phone_used
                or time.monotonic() - session.created_at > PAIRING_TTL_SECONDS
                or not secrets.compare_digest(session.token, credential)
            ):
                return None
            return max(0, int(PAIRING_TTL_SECONDS - (time.monotonic() - session.created_at)))

    async def relay(self, session: PhonePairing, sender: str, message: dict[str, Any]) -> None:
        recipient = "phone" if sender == "computer" else "computer"
        async with self._lock:
            current = self._sessions.get(session.session_id)
            if current is not session:
                return
            peer = session.peers.get(recipient)
            if peer is None:
                if len(session.pending[recipient]) < 64:
                    session.pending[recipient].append(message)
                return
        await peer.send_json(message)

    async def pending_messages(self, session: PhonePairing, role: str) -> list[dict[str, Any]]:
        async with self._lock:
            messages = list(session.pending[role])
            session.pending[role].clear()
            return messages

    async def disconnect(self, session: PhonePairing, role: str) -> WebSocket | None:
        async with self._lock:
            session.peers.pop(role, None)
            peer = session.peers.pop("phone" if role == "computer" else "computer", None)
            self._sessions.pop(session.session_id, None)
            return peer

    def _expire(self) -> None:
        now = time.monotonic()
        expired = [
            key for key, session in self._sessions.items()
            if not session.phone_used and now - session.created_at > PAIRING_TTL_SECONDS
        ]
        for key in expired:
            session = self._sessions.pop(key, None)
            if session:
                for peer in session.peers.values():
                    asyncio.create_task(peer.close(code=1000, reason="Phone pairing expired"))


phone_pairings = PhonePairingStore()
