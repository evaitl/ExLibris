"""Tests for filename sanitization."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from exlibris.cleanup import load_books, sanitize_book_filenames
from exlibris.filenames import (
    build_metadata_filename,
    display_name,
    ensure_safe_filename,
    filename_needs_sanitization,
    has_short_basename,
    sanitize_filename,
    target_filename,
)


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE books (
            id INTEGER PRIMARY KEY,
            file_path TEXT NOT NULL,
            file_name TEXT NOT NULL,
            title TEXT,
            authors TEXT,
            publisher TEXT,
            content_hash TEXT,
            file_size INTEGER NOT NULL DEFAULT 0,
            file_mtime REAL NOT NULL DEFAULT 0,
            is_missing INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    return conn


def test_sanitize_filename_drops_surrogates_and_dangerous_chars() -> None:
    assert sanitize_filename("book\udcc6name.epub") == "bookname.epub"
    assert sanitize_filename('bad:name?.epub') == "badname.epub"
    assert ":" not in sanitize_filename("a:b.epub")


def test_filename_needs_sanitization() -> None:
    assert filename_needs_sanitization("bad:name.epub") is True
    assert filename_needs_sanitization("good-name.epub") is False


def test_ensure_safe_filename_renames_on_disk() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bad = root / "bad\udcc6name.epub"
        bad.write_bytes(b"data")
        fixed = ensure_safe_filename(bad)
        assert fixed.name == "badname.epub"
        assert fixed.is_file()
        assert not bad.exists()


def test_display_name_handles_surrogates() -> None:
    path = Path("/tmp/book\udcc6name.epub")
    printed = display_name(path)
    assert "\udcc6" not in printed


def test_build_metadata_filename() -> None:
    name = build_metadata_filename(
        title="Kim",
        authors="Rudyard Kipling",
        publisher="Penguin",
        fallback_stem="Kim",
    )
    assert name == "Kim - Rudyard Kipling-(Penguin).epub"


def test_target_filename_renames_short_basenames() -> None:
    name = target_filename(
        "Kim.epub",
        title="Kim",
        authors="Rudyard Kipling",
        publisher="Penguin",
    )
    assert name == "Kim - Rudyard Kipling-(Penguin).epub"
    assert has_short_basename("Kim.epub") is True
    assert has_short_basename("Long Enough Title.epub") is False


def test_sanitize_book_filenames_renames_short_basename() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        short = root / "Kim.epub"
        short.write_bytes(b"data")
        conn = _connect(root / "library.db")
        conn.execute(
            """
            INSERT INTO books (
                file_path, file_name, title, authors, publisher,
                file_size, file_mtime
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(short.resolve()),
                short.name,
                "Kim",
                "Rudyard Kipling",
                "Penguin",
                short.stat().st_size,
                short.stat().st_mtime,
            ),
        )
        conn.commit()

        updated, errors = sanitize_book_filenames(conn, [root], execute=True)
        assert errors == []
        assert updated == 1

        book = load_books(conn)[0]
        assert book.file_name == "Kim - Rudyard Kipling-(Penguin).epub"
        assert Path(book.file_path).is_file()
        assert not short.exists()

        conn.close()


def test_sanitize_book_filenames_updates_db() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bad = root / "bad:name-here.epub"
        bad.write_bytes(b"data")
        conn = _connect(root / "library.db")
        conn.execute(
            "INSERT INTO books (file_path, file_name, file_size, file_mtime) VALUES (?, ?, ?, ?)",
            (str(bad.resolve()), bad.name, bad.stat().st_size, bad.stat().st_mtime),
        )
        conn.commit()

        updated, errors = sanitize_book_filenames(conn, [root], execute=True)
        assert errors == []
        assert updated == 1

        book = load_books(conn)[0]
        assert book.file_name == "badname-here.epub"
        assert Path(book.file_path).name == "badname-here.epub"
        assert Path(book.file_path).is_file()
        assert not bad.exists()

        conn.close()


def test_sanitize_book_filenames_dry_run() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bad = root / "bad:name-here.epub"
        bad.write_bytes(b"data")
        conn = _connect(root / "library.db")
        conn.execute(
            "INSERT INTO books (file_path, file_name, file_size, file_mtime) VALUES (?, ?, ?, ?)",
            (str(bad.resolve()), bad.name, bad.stat().st_size, bad.stat().st_mtime),
        )
        conn.commit()

        updated, errors = sanitize_book_filenames(conn, [root], execute=False)
        assert errors == []
        assert updated == 1
        assert bad.is_file()
        assert load_books(conn)[0].file_name == bad.name

        conn.close()
