"""Tests for EPUB validation caching in the database."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

from exlibris.cleanup import (
    audit_epub_integrity,
    collect_epub_paths_for_validation,
    mark_epub_validated_batch,
    update_book_path,
)
from exlibris.epub_validate import EpubValidationResult


def _init_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE books (
            id INTEGER PRIMARY KEY,
            file_path TEXT NOT NULL,
            file_name TEXT NOT NULL,
            file_size INTEGER NOT NULL DEFAULT 0,
            file_mtime REAL NOT NULL DEFAULT 0,
            is_missing INTEGER NOT NULL DEFAULT 0,
            epub_validated INTEGER NOT NULL DEFAULT 0,
            epub_deep_validated INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    return conn


def _insert_book(conn: sqlite3.Connection, path: Path) -> int:
    stat = path.stat()
    cur = conn.execute(
        """
        INSERT INTO books (file_path, file_name, file_size, file_mtime)
        VALUES (?, ?, ?, ?)
        """,
        (str(path), path.name, stat.st_size, stat.st_mtime),
    )
    conn.commit()
    return int(cur.lastrowid)


def test_collect_epub_paths_skips_already_validated() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        good = root / "good.epub"
        good.write_bytes(b"epub")
        pending = root / "pending.epub"
        pending.write_bytes(b"epub2")

        conn = _init_db(root / "library.db")
        good_id = _insert_book(conn, good)
        _insert_book(conn, pending)
        mark_epub_validated_batch(conn, [good_id], deep=False)

        paths, skipped = collect_epub_paths_for_validation(
            conn, [root], deep=False
        )
        assert skipped == 1
        assert paths == [pending.resolve()]


def test_collect_epub_paths_deep_skip_requires_deep_flag() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        book = root / "book.epub"
        book.write_bytes(b"epub")

        conn = _init_db(root / "library.db")
        book_id = _insert_book(conn, book)
        mark_epub_validated_batch(conn, [book_id], deep=False)

        paths, skipped = collect_epub_paths_for_validation(
            conn, [root], deep=True
        )
        assert skipped == 0
        assert paths == [book.resolve()]

        mark_epub_validated_batch(conn, [book_id], deep=True)
        paths, skipped = collect_epub_paths_for_validation(
            conn, [root], deep=True
        )
        assert skipped == 1
        assert paths == []


def test_audit_epub_integrity_marks_valid_books() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        book = root / "book.epub"
        book.write_bytes(b"epub")

        conn = _init_db(root / "library.db")
        book_id = _insert_book(conn, book)
        ok = EpubValidationResult(path=book, ok=True)

        with patch("exlibris.cleanup.validate_epub", return_value=ok):
            invalid, errors = audit_epub_integrity(
                [book.resolve()],
                path_to_book_id={str(book.resolve()): book_id},
                conn=conn,
            )

        assert invalid == []
        assert errors == []
        row = conn.execute(
            "SELECT epub_validated, epub_deep_validated FROM books WHERE id = ?",
            (book_id,),
        ).fetchone()
        assert int(row["epub_validated"]) == 1
        assert int(row["epub_deep_validated"]) == 0


def test_update_book_path_clears_validation_flags() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        old = root / "old.epub"
        new = root / "new-name.epub"
        old.write_bytes(b"epub")
        new.write_bytes(b"epub")

        conn = _init_db(root / "library.db")
        book_id = _insert_book(conn, old)
        mark_epub_validated_batch(conn, [book_id], deep=True)
        update_book_path(conn, book_id, new)

        row = conn.execute(
            "SELECT epub_validated, epub_deep_validated FROM books WHERE id = ?",
            (book_id,),
        ).fetchone()
        assert int(row["epub_validated"]) == 0
        assert int(row["epub_deep_validated"]) == 0
