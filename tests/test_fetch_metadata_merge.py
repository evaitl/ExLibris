"""Tests for online metadata merge behavior."""

from __future__ import annotations

from exlibris.cgi.common import BookRow
from exlibris.fetch_metadata import merge_fetched_metadata, metadata_overwrite_conflicts


def _sample_book(**overrides: object) -> BookRow:
    defaults = {
        "id": 1,
        "file_path": "/books/sample.epub",
        "file_name": "sample.epub",
        "format": "epub",
        "file_size": 100,
        "file_mtime": 1.0,
        "content_hash": "abc",
        "title": "Existing Title",
        "sort_title": None,
        "authors": "Existing Author",
        "publisher": None,
        "published_date": None,
        "isbn": None,
        "language": None,
        "description": None,
        "series": None,
        "series_index": None,
        "page_count": None,
        "cover_path": None,
        "tags": None,
        "first_seen_at": "now",
        "last_scanned_at": "now",
        "is_missing": 0,
    }
    defaults.update(overrides)
    return BookRow(**defaults)


def test_metadata_overwrite_conflicts_detects_existing_changes() -> None:
    book = _sample_book()
    proposed = {
        "title": "New Title",
        "authors": "Existing Author",
        "description": "Added",
        "last_scanned_at": "later",
    }
    assert metadata_overwrite_conflicts(book, proposed) == ["title"]


def test_merge_fetched_metadata_fills_empty_without_confirmation() -> None:
    book = _sample_book(publisher=None, description=None)
    proposed = {
        "title": "New Title",
        "publisher": "New Publisher",
        "description": "Summary",
        "authors": None,
        "last_scanned_at": "later",
    }
    merged = merge_fetched_metadata(book, proposed, confirm_overwrite=False)
    assert merged["publisher"] == "New Publisher"
    assert merged["description"] == "Summary"
    assert merged["last_scanned_at"] == "later"
    assert "title" not in merged
    assert "authors" not in merged


def test_merge_fetched_metadata_overwrites_only_when_confirmed() -> None:
    book = _sample_book()
    proposed = {
        "title": "New Title",
        "authors": "New Author",
        "last_scanned_at": "later",
    }
    without_confirm = merge_fetched_metadata(book, proposed, confirm_overwrite=False)
    assert "title" not in without_confirm
    assert "authors" not in without_confirm

    with_confirm = merge_fetched_metadata(book, proposed, confirm_overwrite=True)
    assert with_confirm["title"] == "New Title"
    assert with_confirm["authors"] == "New Author"
