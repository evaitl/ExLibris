"""Tests for removing invalid EPUB files during cleanup."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from exlibris.cleanup import (
    InvalidEpub,
    build_path_to_book_id,
    remove_invalid_epubs,
)
from exlibris.cover_paths import cover_storage_path


def _init_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE books (
            id INTEGER PRIMARY KEY,
            file_path TEXT NOT NULL,
            file_name TEXT NOT NULL,
            content_hash TEXT,
            file_size INTEGER NOT NULL DEFAULT 0,
            file_mtime REAL NOT NULL DEFAULT 0,
            is_missing INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE user_favorites (
            user_id INTEGER NOT NULL,
            book_id INTEGER NOT NULL,
            PRIMARY KEY (user_id, book_id)
        );
        CREATE TABLE library_stats (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            library_total INTEGER NOT NULL,
            languages TEXT NOT NULL,
            refreshed_at TEXT NOT NULL
        );
        INSERT INTO library_stats (id, library_total, languages, refreshed_at)
        VALUES (1, 0, '[]', 'now');
        """
    )
    return conn


def test_remove_invalid_epubs_deletes_file_and_db_row() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bad = root / "broken.epub"
        bad.write_text("not a zip", encoding="utf-8")
        covers = root / "covers"
        covers.mkdir()
        cover = cover_storage_path(covers, 1, ".jpg")
        cover.parent.mkdir(parents=True)
        cover.write_bytes(b"cover")

        conn = _init_db(root / "library.db")
        conn.execute(
            """
            INSERT INTO books (id, file_path, file_name, file_size, file_mtime)
            VALUES (1, ?, ?, ?, ?)
            """,
            (str(bad.resolve()), bad.name, bad.stat().st_size, bad.stat().st_mtime),
        )
        conn.commit()

        invalid = [
            InvalidEpub(
                path=bad.resolve(),
                book_id=1,
                detail="not a ZIP archive",
            )
        ]
        files_deleted, rows_purged, covers_removed, errors = remove_invalid_epubs(
            conn,
            invalid,
            scan_roots=[root],
            covers_dir=covers,
            execute=True,
        )
        assert errors == []
        assert files_deleted == 1
        assert rows_purged == 1
        assert covers_removed == 1
        assert not bad.exists()
        assert not cover.exists()
        assert conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 0

        conn.close()


def test_remove_invalid_epubs_dry_run_keeps_files() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bad = root / "broken.epub"
        bad.write_text("not a zip", encoding="utf-8")
        conn = _init_db(root / "library.db")
        conn.execute(
            """
            INSERT INTO books (id, file_path, file_name, file_size, file_mtime)
            VALUES (1, ?, ?, ?, ?)
            """,
            (str(bad.resolve()), bad.name, bad.stat().st_size, bad.stat().st_mtime),
        )
        conn.commit()

        invalid = [
            InvalidEpub(path=bad.resolve(), book_id=1, detail="not a ZIP archive")
        ]
        files_deleted, rows_purged, _covers_removed, errors = remove_invalid_epubs(
            conn,
            invalid,
            scan_roots=[root],
            covers_dir=root / "covers",
            execute=False,
        )
        assert errors == []
        assert files_deleted == 1
        assert rows_purged == 1
        assert bad.is_file()
        assert conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 1

        conn.close()


def test_build_path_to_book_id_resolves_paths() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        book = root / "sample.epub"
        book.write_bytes(b"x")
        conn = _init_db(root / "library.db")
        conn.execute(
            "INSERT INTO books (id, file_path, file_name) VALUES (1, ?, ?)",
            (str(book), book.name),
        )
        conn.commit()
        mapping = build_path_to_book_id(conn)
        assert mapping[str(book.resolve())] == 1
        conn.close()
