"""Database engine, sessions, and first-run seed data."""

from __future__ import annotations

import os

from sqlalchemy import create_engine, event, func, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.paths import default_db_url, ensure_dirs

DEFAULT_SETTINGS: dict[str, object] = {
    "confidence": 0.5,
    "pixels_per_mm": 5.0,
    "camera_index": 0,
    "classification_mode": "dimensions",
    "out_of_range": "nearest",
}

DEFAULT_CATEGORIES: list[dict[str, object]] = [
    {"name": "Small", "min_area": 2250, "color": "#5a9a6e", "sort_order": 0},
    {"name": "Medium", "min_area": 2420, "color": "#2d6a4f", "sort_order": 1},
    {"name": "Large", "min_area": 2560, "color": "#d99a2b", "sort_order": 2},
    {"name": "Extra Large", "min_area": 2730, "color": "#c4733b", "sort_order": 3},
    {"name": "Jumbo", "min_area": 2900, "color": "#a23d3d", "sort_order": 4},
]


class Base(DeclarativeBase):
    pass


engine = None
SessionLocal = None


def get_db():
    if SessionLocal is None:
        init_db()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _seed_defaults(db: Session) -> None:
    from backend.models import Category, Setting, User

    # Provision only the explicitly configured administrator identity.
    # There is no public registration; this unusable marker is never accepted as a password.
    admin_email = os.getenv("ADMIN_EMAIL", "").strip().lower()
    if admin_email and "@" in admin_email and db.query(User.id).filter(func.lower(User.email) == admin_email).first() is None:
        db.add(User(email=admin_email, password_hash="!password-not-configured"))

    existing_keys = {row.key for row in db.query(Setting).all()}
    for key, value in DEFAULT_SETTINGS.items():
        if key not in existing_keys:
            db.add(Setting(key=key, value=value))

    if db.query(Category).count() == 0:
        for item in DEFAULT_CATEGORIES:
            db.add(Category(**item))


def _column_names(conn, table: str) -> set[str]:
    return {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}


def _ensure_columns(bound) -> None:
    """Add columns that create_all will not add to an existing SQLite file."""
    if bound.dialect.name != "sqlite":
        return
    additions = {
        "scans": (
            ("operator_email", "VARCHAR(255)"),
            ("model_version", "VARCHAR(128)"),
            ("session_id", "VARCHAR(64)"),
        ),
        "benchmark_trials": (("session_id", "VARCHAR(64)"),),
        "annotations": (
            ("width_mm", "FLOAT"),
            ("pixel_width", "FLOAT"),
            ("geometry", "JSON"),
            ("reference_class_name", "VARCHAR(32)"),
            ("reference_label_source", "VARCHAR(16)"),
            ("reviewed", "BOOLEAN NOT NULL DEFAULT 0"),
        ),
    }
    with bound.begin() as conn:
        for table, columns in additions.items():
            found = _column_names(conn, table)
            if not found:
                continue
            for name, ddl in columns:
                if name not in found:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def _remove_otp_artifacts(bound) -> None:
    """Remove old transient verification data from existing SQLite databases."""
    if bound.dialect.name != "sqlite":
        return
    with bound.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS auth_otp_challenges"))
        if "otp_verified_at" in _column_names(conn, "admin_sessions"):
            conn.execute(text("ALTER TABLE admin_sessions DROP COLUMN otp_verified_at"))
        conn.execute(text("UPDATE users SET password_hash = '!password-not-configured' WHERE password_hash = '!otp-only'"))


def _migrate_size(bound) -> None:
    """Add area, drop gram columns, and move size bands onto area."""
    if bound.dialect.name != "sqlite":
        return
    defaults = {item["name"]: item["min_area"] for item in DEFAULT_CATEGORIES}
    with bound.begin() as conn:
        scan_cols = _column_names(conn, "scans")
        if scan_cols and "area_mm2" not in scan_cols:
            conn.execute(text("ALTER TABLE scans ADD COLUMN area_mm2 FLOAT"))
        if scan_cols:
            conn.execute(text("UPDATE scans SET area_mm2 = ROUND(length_mm * width_mm, 2) WHERE area_mm2 IS NULL"))
        for name in ("est_weight_g", "actual_weight_g"):
            if name in _column_names(conn, "scans"):
                conn.execute(text(f"ALTER TABLE scans DROP COLUMN {name}"))

        cat_cols = _column_names(conn, "categories")
        if cat_cols and "min_area" not in cat_cols:
            conn.execute(text("ALTER TABLE categories ADD COLUMN min_area FLOAT"))
        if cat_cols:
            for class_name, minimum in defaults.items():
                conn.execute(
                    text("UPDATE categories SET min_area = :area WHERE name = :name AND min_area IS NULL"),
                    {"area": minimum, "name": class_name},
                )
            conn.execute(text("UPDATE categories SET min_area = 0 WHERE min_area IS NULL"))
        if "min_weight" in _column_names(conn, "categories"):
            conn.execute(text("ALTER TABLE categories DROP COLUMN min_weight"))
        if _column_names(conn, "settings"):
            conn.execute(text("DELETE FROM settings WHERE key IN ('k', 'a', 'b')"))


def _allow_multiple_ovals(bound) -> None:
    """One photo can hold several egg ovals, so image_key is no longer unique."""
    if bound.dialect.name != "sqlite":
        return
    with bound.begin() as conn:
        if not _column_names(conn, "annotations"):
            return
        indexes = conn.execute(text("PRAGMA index_list(annotations)")).fetchall()
        for row in indexes:
            if not row[2]:
                continue
            info = conn.execute(text(f'PRAGMA index_info("{row[1]}")')).fetchall()
            if [item[2] for item in info] != ["image_key"]:
                continue
            if str(row[1]).startswith("sqlite_autoindex"):
                continue
            conn.execute(text(f'DROP INDEX "{row[1]}"'))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_annotations_image_key ON annotations (image_key)"))


def _allow_pixel_only_annotations(bound) -> None:
    """Allow annotations to retain pixel geometry when physical calibration is unavailable."""
    if bound.dialect.name != "sqlite":
        return
    with bound.begin() as conn:
        info = conn.execute(text("PRAGMA table_info(annotations)")).fetchall()
        length_column = next((row for row in info if row[1] == "length_mm"), None)
        if length_column is None or not length_column[3]:
            return

        columns = [str(row[1]) for row in info]
        definitions = []
        for row in info:
            name, kind, not_null, default, primary_key = row[1], row[2] or "", row[3], row[4], row[5]
            definition = f'"{name}" {kind}'
            if primary_key:
                definition += " PRIMARY KEY"
            elif name not in ("length_mm", "width_mm") and not_null:
                definition += " NOT NULL"
            if default is not None:
                definition += f" DEFAULT {default}"
            definitions.append(definition)

        conn.execute(text("DROP TABLE IF EXISTS annotations_pixel_migration"))
        conn.execute(text("CREATE TABLE annotations_pixel_migration (" + ", ".join(definitions) + ")"))
        quoted = ", ".join(f'"{name}"' for name in columns)
        conn.execute(text(f"INSERT INTO annotations_pixel_migration ({quoted}) SELECT {quoted} FROM annotations"))
        conn.execute(text("DROP TABLE annotations"))
        conn.execute(text("ALTER TABLE annotations_pixel_migration RENAME TO annotations"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_annotations_image_key ON annotations (image_key)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_annotations_class_name ON annotations (class_name)"))


def init_db() -> None:
    global engine, SessionLocal
    ensure_dirs()
    if engine is not None:
        engine.dispose()
    url = os.getenv("DATABASE_URL") or default_db_url()
    connect_args = {"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=connect_args)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    from backend import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_columns(engine)
    _remove_otp_artifacts(engine)
    _allow_pixel_only_annotations(engine)
    _migrate_size(engine)
    _allow_multiple_ovals(engine)
    db = SessionLocal()
    try:
        _seed_defaults(db)
        from backend.services.settings_store import migrate_study_classes

        migrate_study_classes(db)
        db.commit()
    finally:
        db.close()
