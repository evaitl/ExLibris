"""Tests for removing invalid EPUB files during cleanup."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from exlibris.cleanup import (
    EpubRemovalContext,
    EpubRemovalTotals,
    InvalidEpub,
    audit_epub_integrity,
    build_path_to_book_id,
    remove_invalid_epubs,
)
from exlibris.cover_paths import cover_storage_path
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


def test_audit_epub_integrity_streams_invalid_and_valid_milestones(
    monkeypatch,
) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        good_a = root / "a.epub"
        bad = root / "b.epub"
        good_c = root / "c.epub"
        for path in (good_a, bad, good_c):
            path.write_bytes(b"placeholder")

        def fake_validate(path: Path, *, deep: bool = False, ebook_meta_cmd=None):
            del deep, ebook_meta_cmd
            if path.name == "b.epub":
                return EpubValidationResult(
                    path=path, ok=False, errors=["broken spine"]
                )
            return EpubValidationResult(path=path, ok=True, errors=[])

        monkeypatch.setattr("exlibris.cleanup.validate_epub", fake_validate)

        invalids: list[InvalidEpub] = []
        valids: list[tuple[int, int]] = []
        invalid, errors = audit_epub_integrity(
            [good_a, bad, good_c],
            path_to_book_id={str(bad.resolve()): 9},
            on_invalid=invalids.append,
            on_valid_progress=lambda count, total: valids.append((count, total)),
            valid_progress_interval=2,
        )
        assert errors == []
        assert len(invalid) == 1
        assert invalid[0].book_id == 9
        assert invalid[0].detail == "broken spine"
        assert [item.path.name for item in invalids] == ["b.epub"]
        assert valids == [(2, 3)]


def test_audit_epub_integrity_removes_invalid_immediately(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        good = root / "good.epub"
        bad = root / "bad.epub"
        good.write_bytes(b"good")
        bad.write_text("not a zip", encoding="utf-8")
        covers = root / "covers"
        covers.mkdir()

        conn = _init_db(root / "library.db")
        conn.execute(
            """
            INSERT INTO books (id, file_path, file_name, file_size, file_mtime)
            VALUES (1, ?, ?, ?, ?)
            """,
            (str(bad.resolve()), bad.name, bad.stat().st_size, bad.stat().st_mtime),
        )
        conn.commit()

        call_order: list[str] = []

        def fake_validate(path: Path, *, deep: bool = False, ebook_meta_cmd=None):
            del deep, ebook_meta_cmd
            call_order.append(path.name)
            if path.name == "bad.epub":
                return EpubValidationResult(
                    path=path, ok=False, errors=["not a ZIP archive"]
                )
            return EpubValidationResult(path=path, ok=True, errors=[])

        monkeypatch.setattr("exlibris.cleanup.validate_epub", fake_validate)
        monkeypatch.setattr(
            "exlibris.cleanup.convert_epub2_in_place", lambda *_a, **_k: False
        )

        removal_totals = EpubRemovalTotals()
        invalid, errors = audit_epub_integrity(
            [good, bad],
            path_to_book_id=build_path_to_book_id(conn),
            conn=conn,
            removal=EpubRemovalContext(
                scan_roots=[root],
                covers_dir=covers,
                execute=True,
            ),
            removal_totals=removal_totals,
        )
        assert errors == []
        assert len(invalid) == 1
        assert "EPUB 2 conversion failed" in invalid[0].detail
        assert call_order == ["bad.epub", "good.epub"]
        assert not bad.exists()
        assert conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 0
        assert removal_totals.files_deleted == 1
        assert removal_totals.rows_purged == 1
        conn.close()


def test_audit_epub_integrity_converts_then_keeps_when_valid(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bad = root / "repairable.epub"
        bad.write_text("not a zip", encoding="utf-8")
        covers = root / "covers"
        covers.mkdir()

        conn = _init_db(root / "library.db")
        # Add validation columns used by repair recording.
        conn.execute("ALTER TABLE books ADD COLUMN epub_validated INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            "ALTER TABLE books ADD COLUMN epub_deep_validated INTEGER NOT NULL DEFAULT 0"
        )
        conn.execute("ALTER TABLE books ADD COLUMN epub_version2 INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            """
            INSERT INTO books (id, file_path, file_name, file_size, file_mtime)
            VALUES (1, ?, ?, ?, ?)
            """,
            (str(bad.resolve()), bad.name, bad.stat().st_size, bad.stat().st_mtime),
        )
        conn.commit()

        validate_calls = {"n": 0}

        def fake_validate(path: Path, *, deep: bool = False, ebook_meta_cmd=None):
            del deep, ebook_meta_cmd
            validate_calls["n"] += 1
            if validate_calls["n"] == 1:
                return EpubValidationResult(
                    path=path, ok=False, errors=["not a ZIP archive"]
                )
            return EpubValidationResult(path=path, ok=True, errors=[])

        def fake_convert(path: Path, **_kwargs) -> bool:
            path.write_bytes(b"PK\x03\x04repaired-placeholder")
            return True

        monkeypatch.setattr("exlibris.cleanup.validate_epub", fake_validate)
        monkeypatch.setattr("exlibris.cleanup.convert_epub2_in_place", fake_convert)

        removal_totals = EpubRemovalTotals()
        invalid, errors = audit_epub_integrity(
            [bad],
            path_to_book_id=build_path_to_book_id(conn),
            conn=conn,
            removal=EpubRemovalContext(
                scan_roots=[root],
                covers_dir=covers,
                execute=True,
            ),
            removal_totals=removal_totals,
        )
        assert errors == []
        assert invalid == []
        assert bad.is_file()
        assert removal_totals.files_deleted == 0
        row = conn.execute(
            "SELECT epub_validated, epub_version2 FROM books WHERE id = 1"
        ).fetchone()
        assert row["epub_validated"] == 1
        assert row["epub_version2"] == 1
        conn.close()


def test_audit_epub_integrity_dry_run_does_not_convert(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bad = root / "bad.epub"
        bad.write_text("not a zip", encoding="utf-8")
        covers = root / "covers"
        covers.mkdir()
        conn = _init_db(root / "library.db")

        def fake_validate(path: Path, *, deep: bool = False, ebook_meta_cmd=None):
            del deep, ebook_meta_cmd
            return EpubValidationResult(
                path=path, ok=False, errors=["not a ZIP archive"]
            )

        convert_calls = {"n": 0}

        def fake_convert(*_a, **_k) -> bool:
            convert_calls["n"] += 1
            return False

        monkeypatch.setattr("exlibris.cleanup.validate_epub", fake_validate)
        monkeypatch.setattr("exlibris.cleanup.convert_epub2_in_place", fake_convert)

        invalid, errors = audit_epub_integrity(
            [bad],
            path_to_book_id={},
            conn=conn,
            removal=EpubRemovalContext(
                scan_roots=[root],
                covers_dir=covers,
                execute=False,
            ),
        )
        assert errors == []
        assert len(invalid) == 1
        assert convert_calls["n"] == 0
        assert bad.is_file()
        conn.close()
