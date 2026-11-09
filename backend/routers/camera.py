"""ESP32-CAM address and frame capture. Inference stays on this computer."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.auth import get_current_user
from backend.db import get_db
from backend.models import Setting, User
from backend.services.audit import add_log
from backend.services.esp32cam import CameraError, capture, normalize_ip

router = APIRouter(tags=["camera"], dependencies=[Depends(get_current_user)])

_KEY = "esp_camera_ip"


class EspAddress(BaseModel):
    ip: str = Field(min_length=1, max_length=64)


def _stored_ip(db: Session) -> str:
    row = db.get(Setting, _KEY)
    value = row.value if row is not None else ""
    return value.strip() if isinstance(value, str) else ""


def _save_ip(db: Session, address: str) -> None:
    row = db.get(Setting, _KEY)
    if row is None:
        db.add(Setting(key=_KEY, value=address))
    else:
        row.value = address
    db.commit()


@router.get("/api/camera/esp")
def read_esp(db: Session = Depends(get_db)):
    return {"ip": _stored_ip(db)}


@router.post("/api/camera/esp")
def save_esp(
    body: EspAddress,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        address = normalize_ip(body.ip)
        capture(address)
    except CameraError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    _save_ip(db, address)
    add_log(db, "INFO", f"{user.email} configured the ESP32 camera address.")
    db.commit()
    return {"ip": address}


@router.get("/api/camera/esp/frame")
def esp_frame(db: Session = Depends(get_db)):
    address = _stored_ip(db)
    if not address:
        raise HTTPException(status_code=400, detail="Enter the ESP32 IP address.")
    try:
        frame = capture(address)
    except CameraError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return Response(content=frame, media_type="image/jpeg")
