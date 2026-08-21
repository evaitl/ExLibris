from __future__ import annotations

import os
import stat
from pathlib import Path

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine, URL
from sqlalchemy.exc import OperationalError as SQLAlchemyOperationalError
from sqlalchemy.orm import Session, sessionmaker

from exlibris.models import Book

SCHEMA_DIR = Path(__file__).resolve().parent / "schema"
CURRENT_SCHEMA_VERSION = 11


class DatabaseNotWritableError(PermissionError):
    """SQLite cannot write the library database file or its directory."""


def is_readonly_sqlite_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return (
        "readonly" in message
        or "read-only" in message
        or "unable to open database file" in message
    )


def _owner_name(uid: int) -> str:
    try:
        import pwd

        return pwd.getpwuid(uid).pw_name
    except (ImportError, KeyError):
        return str(uid)


def _path_access_line(label: str, path: Path) -> str:
    try:
        st = path.stat()
    except OSError as exc:
        return f"{label}: {path} ({exc})"
    mode = stat.S_IMODE(st.st_mode)
    writable = os.access(path, os.W_OK)
    return (
        f"{label}: mode {mode:04o}, owner {_owner_name(st.st_uid)}, "
        f"{'writable' if writable else 'not writable'}"
    )


def database_not_writable_message(db_path: Path) -> str:
    db_path = Path(db_path).expanduser()
    try:
        db_path = db_path.resolve()
    except OSError:
        pass
    lines = [
        f"Cannot write to the library database at {db_path}.",
    ]
    if db_path.exists():
        lines.append(_path_access_line("  file", db_path))
    else:
        lines.append(f"  file: {db_path} (does not exist yet)")
    lines.append(_path_access_line("  directory", db_path.parent))
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(db_path) + suffix)
        if sidecar.exists() and not os.access(sidecar, os.W_OK):
            lines.append(_path_access_line(f"  {sidecar.name}", sidecar))
    lines.extend(
        [
            "",
            "SQLite needs write access to the database file and its directory",
            "(it creates library.db-wal / library.db-shm next to the database).",
            "Deleting data/library.lock does not fix this — that file only",
            "prevents two maintenance jobs from running at once.",
            "",
            "If you moved the repository (especially with sudo, or onto another disk):",
            f"  sudo chown -R \"$USER:$USER\" {db_path.parent.parent}",
            f"  chmod u+w {db_path.parent} {db_path}",
            "Relative paths in config.json (data/library.db) stay valid after a move.",
            "Recreate the virtualenv so it does not still point at the old tree:",
            "  python3 -m venv .venv && .venv/bin/pip install -e .",
        ]
    )
    return "\n".join(lines)


def ensure_database_writable(db_path: Path) -> Path:
    """Create the parent directory and refuse to open a read-only database."""
    db_path = Path(db_path).expanduser()
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db_path = db_path.resolve()
    except PermissionError as exc:
        raise DatabaseNotWritableError(database_not_writable_message(db_path)) from exc
    if db_path.exists() and not os.access(db_path, os.W_OK):
        raise DatabaseNotWritableError(database_not_writable_message(db_path))
    if not os.access(db_path.parent, os.W_OK):
        raise DatabaseNotWritableError(database_not_writable_message(db_path))
    return db_path


def get_engine(db_path: Path) -> Engine:
    db_path = ensure_database_writable(db_path)
    engine = create_engine(
        URL.create("sqlite", database=str(db_path)),
        future=True,
        connect_args={"check_same_thread": False},
    )
    try:
        with engine.connect() as conn:
            conn.execute(text("PRAGMA foreign_keys = ON"))
            conn.execute(text("PRAGMA journal_mode = WAL"))
            conn.commit()
    except (SQLAlchemyOperationalError, OSError, PermissionError) as exc:
        engine.dispose()
        if isinstance(exc, DatabaseNotWritableError):
            raise
        if is_readonly_sqlite_error(exc) or isinstance(exc, PermissionError):
            raise DatabaseNotWritableError(
                database_not_writable_message(db_path)
            ) from exc
        raise
    return engine


def _schema_version(engine: Engine) -> int | None:
    with engine.connect() as conn:
        tables = conn.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name = 'schema_version'"
            )
        ).fetchone()
        if tables is None:
            return None
        row = conn.execute(text("SELECT MAX(version) FROM schema_version")).fetchone()
        return int(row[0]) if row and row[0] is not None else None


def _apply_schema(engine: Engine, schema_file: Path) -> None:
    ddl = schema_file.read_text(encoding="utf-8")
    with engine.begin() as conn:
        raw = conn.connection.dbapi_connection
        raw.executescript(ddl)


def _apply_migrations(engine: Engine, from_version: int) -> None:
    for version in range(from_version + 1, CURRENT_SCHEMA_VERSION + 1):
        matches = sorted(SCHEMA_DIR.glob(f"{version:03d}_*.sql"))
        if not matches:
            raise FileNotFoundError(f"Missing schema migration for version {version}")
        for schema_file in matches:
            _apply_schema(engine, schema_file)


def init_db(engine: Engine) -> sessionmaker[Session]:
    version = _schema_version(engine)
    if version is None:
        schema_file = SCHEMA_DIR / "001_initial.sql"
        if not schema_file.exists():
            raise FileNotFoundError(f"Missing schema migration: {schema_file}")
        _apply_schema(engine, schema_file)
        version = 1

    if version < CURRENT_SCHEMA_VERSION:
        _apply_migrations(engine, version)
    elif version > CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema version {version} is newer than "
            f"application version {CURRENT_SCHEMA_VERSION}"
        )

    _ensure_author_tokens_backfilled(engine)

    return sessionmaker(bind=engine, expire_on_commit=False)


def _ensure_author_tokens_backfilled(engine: Engine) -> None:
    from exlibris.author_tokens import (
        author_tokens_available,
        author_tokens_table_exists,
        backfill_author_tokens,
    )

    raw = engine.raw_connection()
    try:
        conn = raw
        if not author_tokens_table_exists(conn):
            return
        if author_tokens_available(conn):
            return
        if conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 0:
            return
        backfill_author_tokens(conn)
    finally:
        raw.close()


def find_book_by_content_hash(session: Session, content_hash: str) -> Book | None:
    return session.scalar(
        select(Book)
        .where(Book.content_hash == content_hash)
        .order_by(Book.id)
        .limit(1)
    )


def upsert_book(session: Session, data: dict) -> Book:
    existing = session.scalar(
        select(Book).where(Book.file_path == data["file_path"])
    )
    if existing:
        preserved = {"first_seen_at": existing.first_seen_at}
        file_changed = (
            existing.file_size != data.get("file_size", existing.file_size)
            or existing.file_mtime != data.get("file_mtime", existing.file_mtime)
            or existing.content_hash != data.get("content_hash", existing.content_hash)
        )
        for key, value in data.items():
            if key == "first_seen_at":
                continue
            setattr(existing, key, value)
        existing.first_seen_at = preserved["first_seen_at"]
        existing.is_missing = False
        if file_changed:
            existing.epub_validated = False
            existing.epub_deep_validated = False
            existing.epub_version2 = False
        session.add(existing)
        return existing

    if "first_seen_at" not in data:
        data["first_seen_at"] = data["last_scanned_at"]
    book = Book(is_missing=False, **data)
    session.add(book)
    return book
