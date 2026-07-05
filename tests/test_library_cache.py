"""Tests for library stats cache and list_books performance paths."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from exlibris.cgi.common import _LIST_BOOK_SELECT, list_books, row_to_book
from exlibris.library_cache import (
    cached_languages,
    cached_library_total,
    refresh_library_stats,
)


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
        CREATE VIRTUAL TABLE books_fts USING fts5 (
            title,
            sort_title,
            authors,
            publisher,
            description,
            isbn,
            file_name,
            series,
            tags,
            content='books',
            content_rowid='id',
            tokenize='unicode61 remove_diacritics 2'
        );
        CREATE TRIGGER books_fts_insert
        AFTER INSERT ON books
        BEGIN
            INSERT INTO books_fts (
                rowid, title, sort_title, authors, publisher, description,
                isbn, file_name, series, tags
            ) VALUES (
                new.id, new.title, new.sort_title, new.authors, new.publisher,
                new.description, new.isbn, new.file_name, new.series, new.tags
            );
        END;
        CREATE TABLE library_stats (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            library_total INTEGER NOT NULL,
            languages TEXT NOT NULL,
            refreshed_at TEXT NOT NULL
        );
        """
    )
    return conn


def _insert_book(
    conn: sqlite3.Connection,
    *,
    file_name: str,
    authors: str = "",
    language: str = "en",
    description: str = "long description text",
) -> None:
    path = f"/books/{file_name}"
    conn.execute(
        """
        INSERT INTO books (
            file_path, file_name, title, authors, language, description
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (path, file_name, file_name, authors, language, description),
    )


def test_refresh_library_stats_stores_total_and_languages() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = _init_db(Path(tmp) / "library.db")
        _insert_book(conn, file_name="a.epub", language="en")
        _insert_book(conn, file_name="b.epub", language="fr")
        conn.commit()

        refresh_library_stats(conn)

        assert cached_library_total(conn) == 2
        assert cached_languages(conn) == ["en", "fr"]


def test_list_book_select_omits_description() -> None:
    assert "description" not in _LIST_BOOK_SELECT
    assert "books.description" not in _LIST_BOOK_SELECT


def test_row_to_book_handles_partial_list_row() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = _init_db(Path(tmp) / "library.db")
        _insert_book(conn, file_name="sample.epub", authors="Author")
        conn.commit()
        row = conn.execute(
            f"SELECT {_LIST_BOOK_SELECT} FROM books WHERE id = 1"
        ).fetchone()
        book = row_to_book(row)
        assert book.id == 1
        assert book.description is None
        assert book.file_path == ""


def test_fts_search_skips_count_on_full_page() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = _init_db(Path(tmp) / "library.db")
        for index in range(15):
            _insert_book(
                conn,
                file_name=f"eric-{index}.epub",
                authors="Eric Vall",
            )
        conn.commit()
        refresh_library_stats(conn)

        books, filtered_count, library_total, page, _options, count_exact = list_books(
            conn,
            author="eric vall",
            page=1,
            page_size=10,
        )

        assert len(books) == 10
        assert library_total == 15
        assert filtered_count == 10
        assert count_exact is False


def test_fts_search_exact_count_on_final_partial_page() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = _init_db(Path(tmp) / "library.db")
        for index in range(15):
            _insert_book(
                conn,
                file_name=f"eric-{index}.epub",
                authors="Eric Vall",
            )
        conn.commit()
        refresh_library_stats(conn)

        books, filtered_count, _library_total, page, _options, count_exact = list_books(
            conn,
            author="eric vall",
            page=2,
            page_size=10,
        )

        assert len(books) == 5
        assert filtered_count == 15
        assert count_exact is True
        assert page == 2


def test_unfiltered_browse_uses_cached_library_total() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = _init_db(Path(tmp) / "library.db")
        _insert_book(conn, file_name="one.epub")
        _insert_book(conn, file_name="two.epub")
        conn.commit()
        refresh_library_stats(conn)

        books, filtered_count, library_total, _page, _options, count_exact = list_books(
            conn,
            page=1,
            page_size=10,
        )

        assert len(books) == 2
        assert library_total == 2
        assert filtered_count == 2
        assert count_exact is True
