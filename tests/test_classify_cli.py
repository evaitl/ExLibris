"""Tests for the root classify.py entry point."""

from __future__ import annotations

import importlib.util
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_classify_script():
    script = PROJECT_ROOT / "classify.py"
    spec = importlib.util.spec_from_file_location("classify_script", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_classify_script_keeps_shebang() -> None:
    first = (PROJECT_ROOT / "classify.py").read_text().splitlines()[0]
    assert first == "#!/usr/bin/env python3"


def test_classify_parse_args_defaults() -> None:
    module = _load_classify_script()
    args = module.parse_args([])
    assert args.execute is False
    assert args.overwrite is False
    assert args.limit is None


def test_classify_parse_args_execute_and_path() -> None:
    module = _load_classify_script()
    args = module.parse_args(
        ["--execute", "--path", "/media/books", "--limit", "10", "--overwrite"]
    )
    assert args.execute is True
    assert args.overwrite is True
    assert args.limit == 10
    assert args.path == [Path("/media/books")]
