"""Tests for admin book deletion."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from exlibris.cgi.common import BookRow, delete_book


def _init_db(db_path: Path) -> sqlite3.Connection:
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
            cover_path TEXT,
            title TEXT,
            first_seen_at TEXT NOT NULL DEFAULT 'now',
            last_scanned_at TEXT NOT NULL DEFAULT 'now',
            is_missing INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE user_favorites (
            user_id INTEGER NOT NULL,
            book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            PRIMARY KEY (user_id, book_id)
        );
        """
    )
    return conn


def test_delete_book_removes_file_cover_and_row(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        books_dir = root / "books"
        books_dir.mkdir()
        covers_dir = root / "data" / "covers"
        covers_dir.mkdir(parents=True)
        (covers_dir / "00").mkdir()

        ebook = books_dir / "sample.epub"
        ebook.write_bytes(b"book-bytes")
        cover = covers_dir / "00" / "1.jpg"
        cover.write_bytes(b"cover")

        db_path = root / "library.db"
        conn = _init_db(db_path)
        conn.execute(
            """
            INSERT INTO books (
                id, file_path, file_name, cover_path, title, file_size, file_mtime
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                str(ebook),
                ebook.name,
                "data/covers/00/1.jpg",
                "Sample",
                ebook.stat().st_size,
                ebook.stat().st_mtime,
            ),
        )
        conn.commit()

        monkeypatch.setenv("EXLIBRIS_SCAN_PATHS", str(books_dir))
        monkeypatch.setattr(
            "exlibris.cgi.common.project_root",
            lambda: root,
        )

        book = BookRow(
            id=1,
            file_path=str(ebook),
            file_name=ebook.name,
            format="epub",
            file_size=ebook.stat().st_size,
            file_mtime=ebook.stat().st_mtime,
            content_hash=None,
            title="Sample",
            sort_title=None,
            authors=None,
            publisher=None,
            published_date=None,
            isbn=None,
            language=None,
            description=None,
            series=None,
            series_index=None,
            page_count=None,
            cover_path="data/covers/00/1.jpg",
            tags=None,
            first_seen_at="now",
            last_scanned_at="now",
            is_missing=0,
        )

        delete_book(conn, book)

        assert conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 0
        assert not ebook.exists()
        assert not cover.exists()
        conn.close()
