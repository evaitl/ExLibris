#!/usr/bin/env python3
"""Rename EPUB files with short basenames using title, author, and publisher metadata."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_VENV_PYTHON = _ROOT / ".venv" / "bin" / "python"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from exlibris.cleanup import sanitize_book_filenames


def _ensure_project_python() -> None:
    if os.environ.get("EXLIBRIS_REEXEC") == "1":
        return
    try:
        import pydantic  # noqa: F401
    except ModuleNotFoundError:
        if _VENV_PYTHON.is_file():
            os.environ["EXLIBRIS_REEXEC"] = "1"
            os.execv(str(_VENV_PYTHON), [str(_VENV_PYTHON), *sys.argv])
        print(
            "error: run from the project venv:\n"
            "  .venv/bin/python scripts/rename-short-files.py",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


def _resolve_project_path(path: Path) -> Path:
    path = path.expanduser()
    if not path.is_absolute():
        path = _ROOT / path
    return path.resolve()


def _load_yaml_config(config: Path | None) -> dict:
    path = config.expanduser() if config else _ROOT / "config.yaml"
    if not path.is_file():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _database_path(args: argparse.Namespace) -> Path:
    if args.database is not None:
        return _resolve_project_path(args.database)
    env = os.environ.get("EXLIBRIS_DATABASE_PATH")
    if env:
        return Path(env).expanduser().resolve()
    data = _load_yaml_config(args.config)
    if data.get("database_path"):
        return _resolve_project_path(Path(data["database_path"]))
    return _resolve_project_path(Path("data/library.db"))


def _scan_roots(args: argparse.Namespace) -> list[Path]:
    if args.path:
        return [_resolve_project_path(path) for path in args.path]
    data = _load_yaml_config(args.config)
    raw_paths = data.get("scan_paths")
    if isinstance(raw_paths, list) and raw_paths:
        return [_resolve_project_path(Path(str(path))) for path in raw_paths]
    return [Path("/media/books").resolve()]


def _connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise FileNotFoundError(f"database not found: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def main(argv: list[str] | None = None) -> int:
    _ensure_project_python()

    parser = argparse.ArgumentParser(
        description=(
            "Rename short EPUB basenames and unsafe filenames. "
            "Prefer: exlibris cleanup run --execute"
        )
    )
    parser.add_argument("--database", "-d", type=Path, default=None)
    parser.add_argument("--config", "-c", type=Path, default=None)
    parser.add_argument("--path", "-p", type=Path, action="append", default=None)
    parser.add_argument(
        "--max-stem-len",
        type=int,
        default=10,
        help="Rename when basename stem is shorter than this (default: 10)",
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("-q", "--quiet", action="store_true")
    args = parser.parse_args(argv)

    if args.max_stem_len != 10:
        parser.error(
            "custom --max-stem-len is not supported here; use cleanup_library.py"
        )

    db_path = _database_path(args)
    scan_roots = _scan_roots(args)

    with _connect(db_path) as conn:
        updated, errors = sanitize_book_filenames(
            conn,
            scan_roots,
            execute=args.execute,
            max_short_stem_len=args.max_stem_len,
        )

    if not args.quiet:
        mode = "EXECUTE" if args.execute else "DRY RUN"
        print(f"Database: {db_path}")
        print(f"Mode: {mode}")
        print(f"Updated: {updated}")

    for error in errors:
        print(error, file=sys.stderr)

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
