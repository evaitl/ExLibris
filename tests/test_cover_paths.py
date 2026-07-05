"""Tests for sharded cover path helpers."""

from __future__ import annotations

from pathlib import Path

from exlibris.cover_paths import (
    cover_dest_base,
    cover_public_segment,
    cover_relative_path,
    cover_shard,
    cover_storage_path,
)


def test_cover_shard_uses_last_two_digits() -> None:
    assert cover_shard(1) == "01"
    assert cover_shard(42) == "42"
    assert cover_shard(100) == "00"
    assert cover_shard(12342) == "42"


def test_cover_storage_path_layout() -> None:
    root = Path("/library/data/covers")
    assert cover_storage_path(root, 12342, ".jpg") == root / "42" / "12342.jpg"
    assert cover_dest_base(root, 12342) == root / "42" / "12342"


def test_cover_relative_and_public_segment() -> None:
    project = Path("/proj")
    covers = project / "data" / "covers"
    rel = cover_relative_path(covers, 7, ".jpg", project_root=project)
    assert rel == "data/covers/07/7.jpg"
    assert cover_public_segment(rel) == "07/7.jpg"
