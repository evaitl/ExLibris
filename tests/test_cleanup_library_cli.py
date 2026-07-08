"""CLI argument tests for cleanup_library.py."""

from __future__ import annotations

import importlib.util
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_cleanup_module():
    script = PROJECT_ROOT / "cleanup_library.py"
    spec = importlib.util.spec_from_file_location("cleanup_library", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_args_validate_epubs_only_on_run() -> None:
    module = _load_cleanup_module()
    args = module.parse_args(["run", "--validate-epubs-only"])
    assert args.command == "run"
    assert args.validate_epubs_only is True


def test_normalize_validate_epubs_only_enables_validate_epubs() -> None:
    module = _load_cleanup_module()
    args = module.parse_args(["run", "--validate-epubs-only"])
    assert module._normalize_validate_epub_args(args) is None
    assert args.validate_epubs is True
