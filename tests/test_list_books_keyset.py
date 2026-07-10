"""Tests for keyset pagination in list_books."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from exlibris.cgi.common import list_books
from exlibris.library_cache import refresh_library_stats


def _init_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
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
            sort_title TEXT,
            authors TEXT,
            publisher TEXT,
            published_date TEXT,
            isbn TEXT,
            language TEXT,
            description TEXT,
            series TEXT,
            series_index REAL,
            page_count INTEGER,
            tags TEXT,
            first_seen_at TEXT NOT NULL DEFAULT 'now',
            last_scanned_at TEXT NOT NULL DEFAULT 'now',
            is_missing INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE library_stats (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            library_total INTEGER NOT NULL,
            languages TEXT NOT NULL,
            refreshed_at TEXT NOT NULL
        );
        """
    )
    return conn


def _insert_book(conn: sqlite3.Connection, title: str) -> int:
    file_name = f"{title}.epub"
    cur = conn.execute(
        """
        INSERT INTO books (file_path, file_name, title, sort_title)
        VALUES (?, ?, ?, ?)
        """,
        (f"/books/{file_name}", file_name, title, title),
    )
    conn.commit()
    return int(cur.lastrowid)


def test_list_books_keyset_after_id_matches_offset_page() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = _init_db(Path(tmp) / "library.db")
        titles = [f"Book {index:02d}" for index in range(1, 16)]
        ids = [_insert_book(conn, title) for title in titles]
        refresh_library_stats(conn)

        page1, *_ = list_books(conn, sort="title", page=1, page_size=10)
        page2_offset, *_ = list_books(conn, sort="title", page=2, page_size=10)
        page2_keyset, *_ = list_books(
            conn,
            sort="title",
            page=2,
            page_size=10,
            after_id=page1[-1].id,
        )

        assert [book.id for book in page1] == ids[:10]
        assert [book.id for book in page2_offset] == ids[10:]
        assert [book.id for book in page2_keyset] == ids[10:]
        conn.close()


def test_list_books_keyset_before_id_returns_previous_page() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = _init_db(Path(tmp) / "library.db")
        titles = [f"Book {index:02d}" for index in range(1, 16)]
        ids = [_insert_book(conn, title) for title in titles]
        refresh_library_stats(conn)

        page2, *_ = list_books(conn, sort="title", page=2, page_size=10)
        page1_keyset, *_ = list_books(
            conn,
            sort="title",
            page=1,
            page_size=10,
            before_id=page2[0].id,
        )

        assert [book.id for book in page1_keyset] == ids[:10]
        conn.close()
