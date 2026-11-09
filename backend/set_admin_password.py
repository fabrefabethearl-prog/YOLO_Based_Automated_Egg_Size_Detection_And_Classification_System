"""Set the configured administrator's password without putting it in source or shell history."""

from __future__ import annotations

import os
from getpass import getpass
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import func

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


def main() -> int:
    admin_email = os.getenv("ADMIN_EMAIL", "").strip().lower()
    if not admin_email or "@" not in admin_email:
        print("Set a valid ADMIN_EMAIL in the project's .env first.")
        return 1

    password = getpass("New administrator password (minimum 14 characters): ")
    confirmation = getpass("Repeat password: ")
    if password != confirmation:
        print("Passwords do not match.")
        return 1
    if len(password) < 14:
        print("Use at least 14 characters for the administrator password.")
        return 1
    if len(password.encode("utf-8")) > 72:
        print("Password is too long; use at most 72 UTF-8 bytes.")
        return 1

    from backend.auth import hash_password
    from backend import db as database
    from backend.models import User

    database.init_db()
    db = database.SessionLocal()
    try:
        user = db.query(User).filter(func.lower(User.email) == admin_email).one_or_none()
        if user is None:
            print("The configured administrator account was not found. Check ADMIN_EMAIL and start EggScan once to provision it.")
            return 1
        user.password_hash = hash_password(password)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    print(f"Password set for {admin_email}. It was stored as a bcrypt hash.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
