"""Tests for administrator cover uploads."""

from __future__ import annotations

import struct
import tempfile
from pathlib import Path

import pytest

from exlibris.cgi.common import BookRow, UserRow, upload_cover_action
from exlibris.cgi.render import render_book_detail
from exlibris.cover_paths import cover_storage_path
from exlibris.fetch_metadata import (
    FetchMetadataError,
    MAX_UPLOAD_COVER_BYTES,
    save_uploaded_cover,
)


def _sample_book(**overrides: object) -> BookRow:
    defaults = {
        "id": 7,
        "file_path": "/books/sample.epub",
        "file_name": "sample.epub",
        "format": "epub",
        "file_size": 100,
        "file_mtime": 1.0,
        "content_hash": "abc",
        "title": "Sample Title",
        "sort_title": None,
        "authors": "An Author",
        "publisher": None,
        "published_date": None,
        "isbn": None,
        "language": None,
        "description": None,
        "series": None,
        "series_index": None,
        "page_count": None,
        "cover_path": "data/covers/07/7.jpg",
        "tags": None,
        "first_seen_at": "now",
        "last_scanned_at": "now",
        "is_missing": 0,
    }
    defaults.update(overrides)
    return BookRow(**defaults)


def _jpeg(width: int = 120, height: int = 180) -> bytes:
    sof = bytes(
        [
            0xFF,
            0xC0,
            0x00,
            0x0B,
            0x08,
            (height >> 8) & 0xFF,
            height & 0xFF,
            (width >> 8) & 0xFF,
            width & 0xFF,
            0x01,
            0x01,
            0x11,
            0x00,
        ]
    )
    return b"\xff\xd8" + sof + b"\xff\xd9"


def _png(width: int = 120, height: int = 180) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    )


def test_save_uploaded_cover_writes_jpeg(tmp_path: Path) -> None:
    covers = tmp_path / "covers"
    relative = save_uploaded_cover(_jpeg(), book_id=7, covers_dir=covers)
    dest = cover_storage_path(covers, 7, ".jpg")
    assert dest.is_file()
    assert dest.read_bytes().startswith(b"\xff\xd8")
    assert relative.endswith("07/7.jpg")


def test_save_uploaded_cover_writes_png_and_replaces_jpeg(tmp_path: Path) -> None:
    covers = tmp_path / "covers"
    jpeg_path = cover_storage_path(covers, 7, ".jpg")
    jpeg_path.parent.mkdir(parents=True)
    jpeg_path.write_bytes(b"old-cover")

    relative = save_uploaded_cover(_png(), book_id=7, covers_dir=covers)
    png_path = cover_storage_path(covers, 7, ".png")
    assert png_path.is_file()
    assert png_path.read_bytes().startswith(b"\x89PNG")
    assert not jpeg_path.exists()
    assert relative.endswith("07/7.png")


def test_save_uploaded_cover_rejects_empty() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(FetchMetadataError, match="No cover image"):
            save_uploaded_cover(b"", book_id=1, covers_dir=Path(tmp))


def test_save_uploaded_cover_rejects_too_large(tmp_path: Path) -> None:
    payload = b"\xff\xd8" + b"\x00" * MAX_UPLOAD_COVER_BYTES
    with pytest.raises(FetchMetadataError, match="too large"):
        save_uploaded_cover(payload, book_id=1, covers_dir=tmp_path)


def test_save_uploaded_cover_rejects_non_image(tmp_path: Path) -> None:
    with pytest.raises(FetchMetadataError, match="JPEG or PNG"):
        save_uploaded_cover(b"<html>nope</html>", book_id=1, covers_dir=tmp_path)


def test_save_uploaded_cover_rejects_unreadable_jpeg(tmp_path: Path) -> None:
    with pytest.raises(FetchMetadataError, match="could not be read"):
        save_uploaded_cover(b"\xff\xd8\xff\xd9", book_id=1, covers_dir=tmp_path)


def test_detail_shows_upload_form_for_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("exlibris.cgi.render.is_admin", lambda _user: True)
    html = render_book_detail(_sample_book(), current_user=UserRow(id=1, username="alice"))
    assert 'enctype="multipart/form-data"' in html
    assert upload_cover_action() in html
    assert "Upload cover" in html
    assert 'name="cover"' in html
    assert "Restore cover from file" in html


def test_detail_hides_upload_form_for_non_admin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("exlibris.cgi.render.is_admin", lambda _user: False)
    html = render_book_detail(_sample_book(), current_user=UserRow(id=2, username="bob"))
    assert "upload_cover.py" not in html
    assert "Upload cover" not in html
    assert "Restore cover from file" not in html
