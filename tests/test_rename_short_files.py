"""Tests for rename-short-files helpers."""

from __future__ import annotations

from exlibris.filenames import (
    build_metadata_filename,
    has_short_basename,
    sanitize_filename,
    target_filename,
)


def test_has_short_basename() -> None:
    assert has_short_basename("Kim.epub", max_stem_len=10) is True
    assert has_short_basename("Long Title Name.epub", max_stem_len=10) is False
    assert has_short_basename("notes.txt", max_stem_len=10) is False


def test_build_metadata_filename() -> None:
    name = build_metadata_filename(
        title="Kim",
        authors="Rudyard Kipling",
        publisher="Penguin",
        fallback_stem="Kim",
    )
    assert name == "Kim - Rudyard Kipling-(Penguin).epub"


def test_target_filename_sanitizes_invalid_chars() -> None:
    name = target_filename(
        "Bad.epub",
        title="Bad: Name",
        authors="Author",
        publisher="Pub",
    )
    assert ":" not in name
    assert name.endswith(".epub")
