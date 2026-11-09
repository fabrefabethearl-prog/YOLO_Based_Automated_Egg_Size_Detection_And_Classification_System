"""Request and response models."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_login_email(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if "@" not in cleaned or cleaned.startswith("@") or cleaned.endswith("@"):
            raise ValueError("Enter a valid email address.")
        return cleaned


class OTPEmailRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if "@" not in cleaned or cleaned.startswith("@") or cleaned.endswith("@"):
            raise ValueError("Enter a valid email address.")
        return cleaned


class OTPVerifyRequest(OTPEmailRequest):
    code: str = Field(pattern=r"^\d{6}$")


class FitBandsIn(BaseModel):
    confirm: bool = False


class SettingsIn(BaseModel):
    confidence: float = Field(ge=0, le=1)
    pixels_per_mm: float = Field(gt=0, le=1000)
    camera_index: int = Field(ge=0, le=20)
    classification_mode: str = "dimensions"
    out_of_range: str = "nearest"

    @field_validator("classification_mode")
    @classmethod
    def check_mode(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned not in ("model", "dimensions"):
            raise ValueError("Classification mode must be model or dimensions.")
        return cleaned

    @field_validator("out_of_range")
    @classmethod
    def check_outside(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if cleaned not in ("nearest", "reject"):
            raise ValueError("Eggs outside the five classes must be nearest or reject.")
        return cleaned


class CategoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    min_area: float = Field(ge=0, le=1000000)
    color: str

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Every size class needs a name.")
        return cleaned


class CategoriesIn(BaseModel):
    categories: list[CategoryIn] = Field(min_length=1, max_length=20)


class ScanPatch(BaseModel):
    actual_class: str | None = None

    @field_validator("actual_class")
    @classmethod
    def check_class(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        if len(cleaned) > 32:
            raise ValueError("Size class name is too long.")
        return cleaned


class TrialIn(BaseModel):
    trial_no: int | None = Field(default=None, ge=1, le=100000)
    eggs: int = Field(gt=0, le=10000)
    manual_time_s: float = Field(ge=0, le=86400)
    eggscan_time_s: float = Field(ge=0, le=86400)
    manual_correct: int = Field(ge=0)
    eggscan_correct: int = Field(ge=0)
    session_id: str | None = Field(default=None, max_length=64)

    @field_validator("session_id")
    @classmethod
    def clean_session(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @model_validator(mode="after")
    def correct_within_eggs(self):
        if self.session_id:
            return self
        if self.manual_correct > self.eggs or self.eggscan_correct > self.eggs:
            raise ValueError("Correct counts cannot exceed the number of eggs.")
        return self


class CameraCalibrateIn(BaseModel):
    known_mm: float = Field(gt=0, le=1000)
    pixel_length: float = Field(gt=0, le=100000)
