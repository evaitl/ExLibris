"""Tests for author token search."""

from __future__ import annotations

import sqlite3
import tempfile
import time
from pathlib import Path

from exlibris.author_tokens import (
    backfill_author_tokens,
    sync_author_tokens,
    tokenize_authors,
)
from exlibris.cgi.common import list_books
from exlibris.cgi.search import append_author_token_filters


def test_tokenize_authors_splits_and_normalizes() -> None:
    assert tokenize_authors("Eric Vall") == ["eric", "vall"]
    assert tokenize_authors("Brian Williams; Roderick Gordon") == [
        "brian",
        "williams",
        "roderick",
        "gordon",
    ]


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
            title TEXT,
            sort_title TEXT,
            authors TEXT,
            publisher TEXT,
            series TEXT,
            series_index REAL,
            cover_path TEXT,
            language TEXT,
            is_missing INTEGER NOT NULL DEFAULT 0,
            last_scanned_at TEXT NOT NULL DEFAULT 'now'
        );
        CREATE TABLE book_author_tokens (
            book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
            token TEXT NOT NULL COLLATE NOCASE,
            PRIMARY KEY (book_id, token)
        );
        CREATE INDEX idx_book_author_tokens_token ON book_author_tokens(token, book_id);
        """
    )
    return conn


def test_author_token_search_is_fast_without_fts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = _init_db(Path(tmp) / "library.db")
        for index in range(2000):
            conn.execute(
                """
                INSERT INTO books (file_path, file_name, authors)
                VALUES (?, ?, ?)
                """,
                (
                    f"/books/book-{index}.epub",
                    f"book-{index}.epub",
                    "Eric Vall" if index % 50 == 0 else "Someone Else",
                ),
            )
        conn.commit()
        backfill_author_tokens(conn)

        where: list[str] = ["books.is_missing = 0"]
        params: list[object] = []
        append_author_token_filters(where, params, "eric vall")
        sql = (
            "SELECT COUNT(*) FROM books WHERE "
            + " AND ".join(where)
        )
        started = time.perf_counter()
        count = int(conn.execute(sql, params).fetchone()[0])
        elapsed = time.perf_counter() - started

        assert count == 40
        assert elapsed < 0.5

        books, filtered_count, _total, _page, _options, count_exact = list_books(
            conn,
            author="eric vall",
            page=1,
            page_size=10,
        )
        assert len(books) == 10
        assert filtered_count == 10
        assert count_exact is False

def test_sync_author_tokens_updates_one_book() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = _init_db(Path(tmp) / "library.db")
        conn.execute(
            "INSERT INTO books (id, file_path, file_name, authors) VALUES (1, '/a.epub', 'a.epub', 'Old Name')",
        )
        conn.commit()
        sync_author_tokens(conn, 1, "Eric Vall")
        tokens = [
            row[0]
            for row in conn.execute(
                "SELECT token FROM book_author_tokens WHERE book_id = 1 ORDER BY token"
            )
        ]
        assert tokens == ["eric", "vall"]
