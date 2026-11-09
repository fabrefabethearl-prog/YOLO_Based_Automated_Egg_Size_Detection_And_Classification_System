"""SQLite tables for EggScan."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from backend.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AdminSession(Base):
    """Revocable administrator sessions keyed by a hash of the cookie token."""

    __tablename__ = "admin_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AdminOTPChallenge(Base):
    """Short-lived administrator email verification challenge; never stores the code."""

    __tablename__ = "admin_otp_challenges"

    email: Mapped[str] = mapped_column(String(255), primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    challenge_token_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class Scan(Base):
    __tablename__ = "scans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    image_path: Mapped[str] = mapped_column(String(512))
    length_mm: Mapped[float] = mapped_column(Float)
    width_mm: Mapped[float] = mapped_column(Float)
    area_mm2: Mapped[float] = mapped_column(Float)
    predicted_class: Mapped[str] = mapped_column(String(64), index=True)
    actual_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[float] = mapped_column(Float)
    inference_ms: Mapped[float] = mapped_column(Float)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    operator_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[object] = mapped_column(JSON)


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    min_area: Mapped[float] = mapped_column(Float)
    color: Mapped[str] = mapped_column(String(16))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


class BenchmarkTrial(Base):
    __tablename__ = "benchmark_trials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trial_no: Mapped[int] = mapped_column(Integer, index=True)
    eggs: Mapped[int] = mapped_column(Integer)
    manual_time_s: Mapped[float] = mapped_column(Float)
    eggscan_time_s: Mapped[float] = mapped_column(Float)
    manual_correct: Mapped[int] = mapped_column(Integer)
    eggscan_correct: Mapped[int] = mapped_column(Integer)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Annotation(Base):
    __tablename__ = "annotations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    image_key: Mapped[str] = mapped_column(String(255), index=True)
    class_name: Mapped[str] = mapped_column(String(32), index=True)
    reference_class_name: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reference_label_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    length_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    width_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    start_x: Mapped[float] = mapped_column(Float)
    start_y: Mapped[float] = mapped_column(Float)
    end_x: Mapped[float] = mapped_column(Float)
    end_y: Mapped[float] = mapped_column(Float)
    pixel_length: Mapped[float] = mapped_column(Float)
    pixel_width: Mapped[float | None] = mapped_column(Float, nullable=True)
    geometry: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    calibration: Mapped[str] = mapped_column(String(16))
    pixels_per_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Log(Base):
    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    level: Mapped[str] = mapped_column(String(16), index=True)
    message: Mapped[str] = mapped_column(Text)
