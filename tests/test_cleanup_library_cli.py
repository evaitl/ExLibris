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


def test_progress_callback_quiet_is_none() -> None:
    module = _load_cleanup_module()
    assert module._progress_callback(quiet=True, verbose=False, label="hash") is None


def test_progress_callback_emits_milestones(capsys) -> None:
    module = _load_cleanup_module()
    on_progress = module._progress_callback(
        quiet=False, verbose=False, label="hash"
    )
    assert on_progress is not None
    on_progress(1, 50, "a.epub")
    on_progress(2, 50, "b.epub")
    on_progress(25, 50, "c.epub")
    on_progress(50, 50, "d.epub")
    out = capsys.readouterr().out
    assert "hash [ 1/50] a.epub" in out
    assert "b.epub" not in out
    assert "hash [25/50] c.epub" in out
    assert "hash [50/50] d.epub" in out


def test_progress_callback_verbose_emits_every_item(capsys) -> None:
    module = _load_cleanup_module()
    on_progress = module._progress_callback(
        quiet=False, verbose=True, label="validate"
    )
    assert on_progress is not None
    on_progress(2, 3, "mid.epub")
    assert "validate [2/3] mid.epub" in capsys.readouterr().out


def test_epub_validation_live_callbacks_quiet() -> None:
    module = _load_cleanup_module()
    assert module._epub_validation_live_callbacks(quiet=True) == (None, None)


def test_epub_validation_live_callbacks_report_invalid_and_valid(capsys) -> None:
    module = _load_cleanup_module()
    on_invalid, on_valid_progress = module._epub_validation_live_callbacks(
        quiet=False
    )
    assert on_invalid is not None and on_valid_progress is not None
    from exlibris.cleanup import InvalidEpub

    on_invalid(
        InvalidEpub(
            path=Path("/books/bad.epub"),
            book_id=7,
            detail="not a ZIP archive",
        )
    )
    on_valid_progress(1000, 5000)
    out = capsys.readouterr().out
    assert "invalid: id=7  /books/bad.epub: not a ZIP archive" in out
    assert "valid [1000/5000] checked OK" in out
