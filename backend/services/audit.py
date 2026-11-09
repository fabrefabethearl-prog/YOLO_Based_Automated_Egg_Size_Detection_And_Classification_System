"""Activity log helpers."""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend import db as database
from backend.models import Log, utcnow


def add_log(db: Session, level: str, message: str) -> None:
    db.add(Log(level=level.upper(), message=message[:2000], created_at=utcnow()))


def write_log(level: str, message: str) -> None:
    """Write a log from outside a request session, such as an unhandled error."""
    if database.SessionLocal is None:
        return
    db = database.SessionLocal()
    try:
        add_log(db, level, message)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()
