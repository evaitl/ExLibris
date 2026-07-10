"""Tests for EPUB validation during library scan."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from exlibris.database import get_engine, init_db
from exlibris.ebook_meta import EbookMeta
from exlibris.models import Book
from exlibris.scanner import scan_paths, scan_single_file
from tests.test_epub_validate import _write_minimal_epub


def test_scan_single_file_skips_invalid_epub_before_index() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bad = root / "broken.epub"
        bad.write_text("not a zip", encoding="utf-8")

        engine = get_engine(root / "library.db")
        SessionLocal = init_db(engine)

        with SessionLocal() as session:
            with patch("exlibris.scanner.read_metadata") as read_metadata:
                result = scan_single_file(
                    session,
                    bad,
                    scan_roots=[root],
                    validate_epub=True,
                )
                read_metadata.assert_not_called()

            assert result.status == "invalid_epub"
            assert "ZIP" in result.detail
            assert result.files_deleted == 1
            assert not bad.is_file()
            assert session.scalar(select(Book)) is None


def test_scan_single_file_indexes_valid_epub_and_marks_validated() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        good = root / "good.epub"
        _write_minimal_epub(good)
        meta = EbookMeta(
            title="Sample",
            authors="Author",
            format="epub",
        )

        engine = get_engine(root / "library.db")
        SessionLocal = init_db(engine)
        now = datetime.now(timezone.utc)

        with SessionLocal() as session:
            with (
                patch("exlibris.scanner.read_metadata", return_value=meta),
                patch("exlibris.scanner.extract_cover", return_value=None),
            ):
                result = scan_single_file(
                    session,
                    good,
                    now=now,
                    scan_roots=[root],
                    validate_epub=True,
                )

            book = session.scalar(select(Book))
            assert result.status == "indexed"
            assert book is not None
            assert book.title == "Sample"
            assert book.epub_validated is True
            assert book.epub_deep_validated is False


def test_scan_paths_deletes_invalid_epub_and_does_not_reprocess() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        good = root / "good.epub"
        bad = root / "bad.epub"
        _write_minimal_epub(good)
        bad.write_text("not a zip", encoding="utf-8")
        meta = EbookMeta(title="Sample", authors="Author", format="epub")

        engine = get_engine(root / "library.db")
        SessionLocal = init_db(engine)

        with SessionLocal() as session:
            with (
                patch("exlibris.scanner.read_metadata", return_value=meta),
                patch("exlibris.scanner.extract_cover", return_value=None),
            ):
                stats = scan_paths(session, [root], validate_epub=True)

        assert stats.invalid_epubs == 1
        assert stats.files_deleted == 1
        assert stats.added_or_updated == 1
        assert not bad.is_file()
        assert any("bad.epub" in err for err in stats.errors)

        with SessionLocal() as session:
            with (
                patch("exlibris.scanner.read_metadata", return_value=meta),
                patch("exlibris.scanner.extract_cover", return_value=None),
            ):
                second = scan_paths(session, [root], validate_epub=True)

        assert second.invalid_epubs == 0
        assert second.scanned == 1
        assert second.added_or_updated == 0
