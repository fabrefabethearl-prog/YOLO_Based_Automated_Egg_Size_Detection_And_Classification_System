"""Activity log."""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import String, case, cast, func, or_
from sqlalchemy.orm import Session

from backend.auth import get_current_user
from backend.db import get_db
from backend.models import Log, User
from backend.services.audit import add_log
from backend.services.serialize import isoformat

router = APIRouter(prefix="/api/logs", tags=["logs"], dependencies=[Depends(get_current_user)])

LOG_CATEGORIES = ("authentication", "scan", "verification", "dataset", "training", "system")
_ACTOR = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
_SECRET_ASSIGNMENT = re.compile(r"(?i)\b(password|passwd|(?:access|refresh)[_-]?token|token|client[_-]?secret|secret|authorization|cookie|api[_-]?key)\b\s*[:=]\s*([^\s,;]+)")
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")


def _category_conditions():
    message = func.lower(Log.message)
    is_auth = message.like("authentication:%")
    is_training = or_(message.like("%started yolo training%"), message.like("%yolo training finished%"), message.like("%yolo training failed%"), message.like("%yolo training could not be activated%"))
    is_dataset = or_(message.like("%dataset/%"), message.like("%class folder%"), message.like("%prepared a yolo detection dataset%"), message.like("%deleted class images%"))
    is_verification = or_(message.like("%verified scan records scan #%"), message.like("%cleared the actual size class on scan records%"), message.like("%fitted size bands from%"))
    is_scan = or_(message.like("scan failed:%"), message.like("%saved live scan record #%"), message.like("%re-classify%scan #%"), message.like("%scan #%"), message.like("no egg detected%"))
    return {
        "authentication": is_auth,
        "training": is_training,
        "dataset": is_dataset,
        "verification": is_verification,
        "scan": is_scan,
        "system": ~or_(is_auth, is_training, is_dataset, is_verification, is_scan),
    }


def _category_expression():
    conditions = _category_conditions()
    return case(
        (conditions["authentication"], "authentication"),
        (conditions["training"], "training"),
        (conditions["dataset"], "dataset"),
        (conditions["verification"], "verification"),
        (conditions["scan"], "scan"),
        else_="system",
    )


def _actor(message: str) -> str | None:
    match = _ACTOR.search(message or "")
    return match.group(0) if match else None


def _category_for_message(message: str) -> str:
    text = (message or "").lower()
    if text.startswith("authentication:"):
        return "authentication"
    if any(term in text for term in ("started yolo training", "yolo training finished", "yolo training failed", "yolo training could not be activated")):
        return "training"
    if any(term in text for term in ("dataset/", "class folder", "prepared a yolo detection dataset", "deleted class images")):
        return "dataset"
    if any(term in text for term in ("verified scan records scan #", "cleared the actual size class on scan records", "fitted size bands from")):
        return "verification"
    if any(term in text for term in ("scan failed:", "saved live scan record #", "re-classify", "scan #", "no egg detected")):
        return "scan"
    return "system"


def _safe_message(message: str) -> str:
    safe = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", message or "")
    safe = _BEARER.sub("Bearer [REDACTED]", safe)
    return _JWT.sub("[REDACTED TOKEN]", safe)


def _parse_day(value: str, name: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{name} must be a valid date in YYYY-MM-DD format.") from exc


@router.get("")
def list_logs(
    level: str = "",
    page: int = 1,
    page_size: int = 20,
    q: str = "",
    actor: str = "",
    category: str = "",
    date_from: str = "",
    date_to: str = "",
    db: Session = Depends(get_db),
):
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    query = db.query(Log)
    if len(q) > 200:
        raise HTTPException(status_code=400, detail="Search text must be 200 characters or fewer.")
    if len(actor) > 255:
        raise HTTPException(status_code=400, detail="Actor filter must be 255 characters or fewer.")
    start_day = _parse_day(date_from, "Start date")
    end_day = _parse_day(date_to, "End date")
    if start_day and end_day and end_day < start_day:
        raise HTTPException(status_code=400, detail="End date must be on or after the start date.")
    q = q.strip()
    actor = actor.strip()
    if q:
        query = query.filter(or_(Log.message.contains(q, autoescape=True), cast(Log.id, String) == q))
    if actor:
        query = query.filter(Log.message.contains(actor, autoescape=True))
    if category:
        if category not in LOG_CATEGORIES:
            raise HTTPException(status_code=400, detail="Choose a supported activity category.")
        query = query.filter(_category_expression() == category)
    if start_day:
        query = query.filter(Log.created_at >= datetime.combine(start_day, time.min, tzinfo=timezone.utc))
    if end_day:
        next_day = end_day + timedelta(days=1)
        query = query.filter(Log.created_at < datetime.combine(next_day, time.min, tzinfo=timezone.utc))

    matching_query = query
    all_total = db.query(Log).count()
    level_counts = {name: int(count) for name, count in matching_query.with_entities(Log.level, func.count(Log.id)).group_by(Log.level).all()}
    recent_cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    recent_count = matching_query.filter(Log.created_at >= recent_cutoff).count()
    if level:
        normalized = level.upper()
        if normalized not in {"INFO", "WARN", "ERROR"}:
            raise HTTPException(status_code=400, detail="Level must be INFO, WARN, or ERROR.")
        query = query.filter(Log.level == normalized)
    total = query.count()
    pages = max((total + page_size - 1) // page_size, 1)
    page = min(page, pages)
    rows = query.order_by(Log.created_at.desc(), Log.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
    return {
        "items": [
            {
                "id": row.id,
                "created_at": isoformat(row.created_at),
                "level": row.level,
                "message": _safe_message(row.message),
                "category": _category_for_message(row.message),
                "actor": _actor(row.message),
            }
            for row in rows
        ],
        "total": total,
        "all_total": all_total,
        "level_counts": level_counts,
        "recent_count": recent_count,
        "page": page,
        "page_size": page_size,
        "pages": pages,
    }


@router.delete("")
def clear_logs(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    deleted_count = db.query(Log).count()
    db.query(Log).delete(synchronize_session=False)
    add_log(db, "INFO", f"{user.email} cleared logs ({deleted_count} previous entries).")
    db.commit()
    return {"ok": True, "deleted_count": deleted_count}
