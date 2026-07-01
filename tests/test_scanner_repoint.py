"""Tests for scanner duplicate repoint and on-disk cleanup."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from exlibris.database import get_engine, init_db
from exlibris.file_hash import sha1_file
from exlibris.models import Book
from exlibris.scanner import scan_single_file


def test_scan_repoint_deletes_shorter_duplicate_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        short = root / "short.epub"
        long = root / "much-longer-title.epub"
        short.write_bytes(b"same-content")
        long.write_bytes(b"same-content")
        content_hash = sha1_file(long)

        db_path = root / "library.db"
        engine = get_engine(db_path)
        SessionLocal = init_db(engine)
        now = datetime.now(timezone.utc)

        with SessionLocal() as session:
            book = Book(
                file_path=str(short.resolve()),
                file_name=short.name,
                format="epub",
                file_size=short.stat().st_size,
                file_mtime=short.stat().st_mtime,
                content_hash=content_hash,
                title="Short title",
                first_seen_at=now,
                last_scanned_at=now,
            )
            session.add(book)
            session.commit()

            result = scan_single_file(
                session,
                long,
                scan_roots=[root],
            )

            session.refresh(book)

        assert result.status == "repointed"
        assert result.files_deleted == 1
        assert not short.exists()
        assert long.exists()
        assert Path(book.file_path) == long.resolve()
        assert book.file_name == long.name
