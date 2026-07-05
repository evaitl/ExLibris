"""Tests for rename-short-files helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "rename-short-files.py"
_spec = importlib.util.spec_from_file_location("rename_short_files", _SCRIPT)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
import sys

sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)


def test_has_short_basename() -> None:
    assert _mod.has_short_basename("Kim.epub", max_stem_len=10) is True
    assert _mod.has_short_basename("Long Title Name.epub", max_stem_len=10) is False
    assert _mod.has_short_basename("notes.txt", max_stem_len=10) is False


def test_build_target_file_name() -> None:
    name = _mod.build_target_file_name(
        title="Kim",
        authors="Rudyard Kipling",
        publisher="Penguin",
        fallback_stem="Kim",
    )
    assert name == "Kim - Rudyard Kipling-(Penguin).epub"


def test_build_target_file_name_sanitizes_invalid_chars() -> None:
    name = _mod.build_target_file_name(
        title='Bad: Name',
        authors="Author",
        publisher="Pub",
        fallback_stem="Bad",
    )
    assert ":" not in name
    assert name.endswith(".epub")
