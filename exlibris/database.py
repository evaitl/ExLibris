from pathlib import Path

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from exlibris.models import Book

SCHEMA_DIR = Path(__file__).resolve().parent / "schema"
CURRENT_SCHEMA_VERSION = 11


def get_engine(db_path: Path) -> Engine:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_path.resolve()}",
        future=True,
        connect_args={"check_same_thread": False},
    )
    with engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys = ON"))
        conn.execute(text("PRAGMA journal_mode = WAL"))
        conn.commit()
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
