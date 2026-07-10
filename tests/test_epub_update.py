"""Tests for EPUB 2 conversion and update_epubs processing."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

from exlibris.database import get_engine, init_db
from exlibris.epub_update import list_epubs_needing_update, update_epubs
from exlibris.epub_validate import EpubValidationResult


def _init_db(db_path: Path) -> sqlite3.Connection:
    engine = get_engine(db_path)
    init_db(engine)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _insert_epub(
    conn: sqlite3.Connection,
    *,
    book_id: int,
    path: Path,
    epub_version2: int = 0,
    content_hash: str | None = None,
) -> None:
    stat = path.stat()
    if content_hash is None:
        content_hash = f"hash{book_id}"
    conn.execute(
        """
        INSERT INTO books (
            id, file_path, file_name, format, file_size, file_mtime,
            content_hash, first_seen_at, last_scanned_at, is_missing,
            epub_version2
        ) VALUES (?, ?, ?, 'epub', ?, ?, ?, 'now', 'now', 0, ?)
        """,
        (
            book_id,
            str(path),
            path.name,
            stat.st_size,
            stat.st_mtime,
            content_hash,
            epub_version2,
        ),
    )
    conn.commit()


def test_list_epubs_needing_update_skips_converted() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        pending = root / "pending.epub"
        done = root / "done.epub"
        pending.write_bytes(b"pending")
        done.write_bytes(b"done")
        conn = _init_db(root / "library.db")
        _insert_epub(conn, book_id=1, path=pending, epub_version2=0)
        _insert_epub(conn, book_id=2, path=done, epub_version2=1)

        candidates, skipped = list_epubs_needing_update(conn, [root])
        assert skipped == 1
        assert len(candidates) == 1
        assert candidates[0].id == 1
        conn.close()


def test_update_epubs_dry_run_counts_without_changes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / "book.epub"
        path.write_bytes(b"epub")
        conn = _init_db(root / "library.db")
        _insert_epub(conn, book_id=1, path=path, content_hash="abc")

        stats = update_epubs(
            conn,
            [root],
            root / "covers",
            execute=False,
        )
        assert stats.converted == 1
        assert stats.removed == 0
        row = conn.execute(
            "SELECT epub_version2, content_hash FROM books WHERE id = 1"
        ).fetchone()
        assert int(row["epub_version2"]) == 0
        assert row["content_hash"] == "abc"
        assert path.read_bytes() == b"epub"
        conn.close()


def test_update_epubs_execute_marks_converted() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / "book.epub"
        path.write_bytes(b"old")
        covers = root / "covers"
        covers.mkdir()
        conn = _init_db(root / "library.db")
        _insert_epub(conn, book_id=1, path=path, content_hash="abc")

        def fake_convert(source: Path, dest: Path, *, ebook_convert_cmd=None):
            del source, ebook_convert_cmd
            dest.write_bytes(b"new epub 2")

        def fake_validate(path: Path) -> EpubValidationResult:
            return EpubValidationResult(path=path, ok=True)

        with patch("exlibris.epub_update.convert_epub_to_version2", side_effect=fake_convert):
            with patch("exlibris.epub_update.validate_epub_structure", side_effect=fake_validate):
                with patch(
                    "exlibris.epub_update._refresh_cover_from_epub",
                    return_value="data/covers/01/1.jpg",
                ) as refresh:
                    stats = update_epubs(
                        conn,
                        [root],
                        covers,
                        execute=True,
                    )

        refresh.assert_called_once()
        assert stats.converted == 1
        assert stats.removed == 0
        assert path.read_bytes() == b"new epub 2"
        row = conn.execute(
            "SELECT epub_version2, epub_validated, cover_path, content_hash FROM books WHERE id = 1"
        ).fetchone()
        assert int(row["epub_version2"]) == 1
        assert int(row["epub_validated"]) == 1
        assert row["cover_path"] == "data/covers/01/1.jpg"
        assert row["content_hash"] != "abc"
        assert oct(path.stat().st_mode & 0o777) == "0o644"
        conn.close()


def test_update_epubs_execute_removes_failed_validation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / "bad.epub"
        path.write_bytes(b"bad")
        covers = root / "covers"
        covers.mkdir()
        conn = _init_db(root / "library.db")
        _insert_epub(conn, book_id=1, path=path, content_hash="abc")

        def fake_convert(source: Path, dest: Path, *, ebook_convert_cmd=None):
            del source, ebook_convert_cmd
            dest.write_bytes(b"converted")

        def fake_validate(path: Path) -> EpubValidationResult:
            return EpubValidationResult(path=path, ok=False, errors=["broken spine"])

        with patch("exlibris.epub_update.convert_epub_to_version2", side_effect=fake_convert):
            with patch("exlibris.epub_update.validate_epub_structure", side_effect=fake_validate):
                stats = update_epubs(
                    conn,
                    [root],
                    covers,
                    execute=True,
                )

        assert stats.converted == 0
        assert stats.removed == 1
        assert not path.exists()
        assert conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 0
        conn.close()


def test_update_epubs_execute_removes_failed_conversion() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / "bad.epub"
        path.write_bytes(b"bad")
        covers = root / "covers"
        covers.mkdir()
        conn = _init_db(root / "library.db")
        _insert_epub(conn, book_id=1, path=path, content_hash="abc")

        def fake_convert(source: Path, dest: Path, *, ebook_convert_cmd=None):
            del source, dest, ebook_convert_cmd
            raise RuntimeError("convert failed")

        from exlibris.ebook_convert import EbookConvertError

        with patch(
            "exlibris.epub_update.convert_epub_to_version2",
            side_effect=EbookConvertError("convert failed"),
        ):
            stats = update_epubs(
                conn,
                [root],
                covers,
                execute=True,
            )

        assert stats.converted == 0
        assert stats.removed == 1
        assert not path.exists()
        assert conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 0
        conn.close()


def test_update_epubs_logs_success_milestones_and_failures() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        conn = _init_db(root / "library.db")
        events: list[tuple[str, str]] = []

        def on_event(
            current: int,
            total: int,
            name: str,
            event: str,
            detail: str,
        ) -> None:
            del current, total
            events.append((event, detail))

        for book_id in range(1, 4):
            path = root / f"book{book_id}.epub"
            path.write_bytes(f"epub{book_id}".encode())
            _insert_epub(conn, book_id=book_id, path=path, content_hash=f"hash{book_id}")

        def fake_convert(source: Path, dest: Path, *, ebook_convert_cmd=None):
            del ebook_convert_cmd
            if source.name == "book2.epub":
                from exlibris.ebook_convert import EbookConvertError

                raise EbookConvertError("convert failed")
            dest.write_bytes(source.read_bytes() + b"-converted")

        def fake_validate(path: Path) -> EpubValidationResult:
            return EpubValidationResult(path=path, ok=True)

        with patch("exlibris.epub_update.convert_epub_to_version2", side_effect=fake_convert):
            with patch("exlibris.epub_update.validate_epub_structure", side_effect=fake_validate):
                stats = update_epubs(
                    conn,
                    [root],
                    root / "covers",
                    execute=True,
                    on_event=on_event,
                )

        assert stats.converted == 2
        assert stats.removed == 1
        assert ("converted", "") in events
        assert ("removed", "convert failed") in events
        assert events.count(("converted", "")) == 2
        conn.close()
