"""Integration tests for library cleanup."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from exlibris.cleanup import (
    BookRecord,
    apply_duplicate_group,
    audit_library,
    build_duplicate_groups,
)


def _init_books_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT NOT NULL UNIQUE,
            file_name TEXT NOT NULL,
            format TEXT NOT NULL DEFAULT 'epub',
            file_size INTEGER NOT NULL DEFAULT 0,
            file_mtime REAL NOT NULL DEFAULT 0,
            content_hash TEXT,
            is_missing INTEGER NOT NULL DEFAULT 0,
            first_seen_at TEXT NOT NULL DEFAULT 'now',
            last_scanned_at TEXT NOT NULL DEFAULT 'now'
        );
        CREATE UNIQUE INDEX idx_books_content_hash ON books (content_hash)
            WHERE content_hash IS NOT NULL;
        """
    )
    return conn


def test_apply_duplicate_group_dedupes_and_repoints() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        short = root / "short.epub"
        long = root / "much-longer-title.epub"
        short.write_bytes(b"same-content")
        long.write_bytes(b"same-content")

        db_path = root / "library.db"
        conn = _init_books_db(db_path)
        conn.execute(
            """
            INSERT INTO books (file_path, file_name, content_hash, file_size, file_mtime)
            VALUES (?, ?, ?, ?, ?)
            """,
            (str(short), short.name, "hash1", short.stat().st_size, short.stat().st_mtime),
        )
        conn.commit()

        groups = build_duplicate_groups(
            {"hash1": [long.resolve()]},
            {
                "hash1": BookRecord(
                    id=1,
                    file_path=str(short),
                    file_name=short.name,
                    content_hash="hash1",
                    is_missing=False,
                )
            },
        )
        assert len(groups) == 1
        rows_updated, files_deleted, repointed_id = apply_duplicate_group(
            conn,
            groups[0],
            scan_roots=[root],
            execute=True,
        )
        assert rows_updated == 1
        assert files_deleted == 1
        assert repointed_id == 1
        assert not short.exists()
        assert long.exists()
        row = conn.execute("SELECT file_path FROM books WHERE id = 1").fetchone()
        assert row is not None
        assert Path(str(row["file_path"])) == long.resolve()
        conn.close()


def test_audit_finds_unindexed_duplicate() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        indexed = root / "indexed.epub"
        duplicate = root / "duplicate-copy.epub"
        indexed.write_bytes(b"book-bytes")
        duplicate.write_bytes(b"book-bytes")

        db_path = root / "library.db"
        conn = _init_books_db(db_path)
        conn.execute(
            """
            INSERT INTO books (file_path, file_name, content_hash, file_size, file_mtime)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(indexed),
                indexed.name,
                "abc123",
                indexed.stat().st_size,
                indexed.stat().st_mtime,
            ),
        )
        conn.commit()

        from exlibris.file_hash import sha1_file

        content_hash = sha1_file(indexed)
        conn.execute(
            "UPDATE books SET content_hash = ? WHERE id = 1",
            (content_hash,),
        )
        conn.commit()

        report = audit_library(conn, [root], covers_dir=root / "covers")
        assert len(report.unindexed_files) == 1
        assert report.unindexed_files[0].resolve() == duplicate.resolve()
        assert len(report.duplicate_groups) == 1
        assert report.duplicate_groups[0].keeper.name == "duplicate-copy.epub"
        conn.close()
