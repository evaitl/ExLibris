"""Tests for neighbor_book_ids keyset navigation."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from exlibris.cgi.common import LibraryBrowseContext, neighbor_book_ids


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
            cover_path TEXT,
            tags TEXT,
            first_seen_at TEXT NOT NULL DEFAULT 'now',
            last_scanned_at TEXT NOT NULL DEFAULT 'now',
            is_missing INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    return conn


def _insert_book(
    conn: sqlite3.Connection,
    *,
    title: str,
    authors: str = "",
    language: str | None = None,
    file_name: str | None = None,
) -> int:
    file_name = file_name or f"{title}.epub"
    file_path = f"/books/{file_name}"
    cur = conn.execute(
        """
        INSERT INTO books (file_path, file_name, title, authors, language)
        VALUES (?, ?, ?, ?, ?)
        """,
        (file_path, file_name, title, authors, language),
    )
    conn.commit()
    return int(cur.lastrowid)


def test_neighbor_book_ids_returns_adjacent_titles_in_sort_order() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = _init_db(Path(tmp) / "library.db")
        alpha_id = _insert_book(conn, title="Alpha")
        beta_id = _insert_book(conn, title="Beta")
        gamma_id = _insert_book(conn, title="Gamma")
        ctx = LibraryBrowseContext(sort="title", sort_dir="asc")

        prev_id, next_id = neighbor_book_ids(conn, beta_id, ctx)
        assert prev_id == alpha_id
        assert next_id == gamma_id

        first_prev, first_next = neighbor_book_ids(conn, alpha_id, ctx)
        assert first_prev is None
        assert first_next == beta_id

        last_prev, last_next = neighbor_book_ids(conn, gamma_id, ctx)
        assert last_prev == beta_id
        assert last_next is None


def test_neighbor_book_ids_respects_title_desc_sort() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = _init_db(Path(tmp) / "library.db")
        alpha_id = _insert_book(conn, title="Alpha")
        beta_id = _insert_book(conn, title="Beta")
        gamma_id = _insert_book(conn, title="Gamma")
        ctx = LibraryBrowseContext(sort="title", sort_dir="desc")

        prev_id, next_id = neighbor_book_ids(conn, beta_id, ctx)
        assert prev_id == gamma_id
        assert next_id == alpha_id


def test_neighbor_book_ids_returns_none_when_book_not_in_filter() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = _init_db(Path(tmp) / "library.db")
        alpha_id = _insert_book(conn, title="Alpha", language="en")
        beta_id = _insert_book(conn, title="Beta", language="fr")
        ctx = LibraryBrowseContext(language="en")

        prev_id, next_id = neighbor_book_ids(conn, beta_id, ctx)
        assert prev_id is None
        assert next_id is None

        only_prev, only_next = neighbor_book_ids(conn, alpha_id, ctx)
        assert only_prev is None
        assert only_next is None
