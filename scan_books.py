#!/usr/bin/env python3
"""Walk directory trees and populate the ExLibris database using ebook-meta."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_VENV_PYTHON = _ROOT / ".venv" / "bin" / "python"


def _ensure_project_python() -> None:
    """Re-exec with the project venv when started via system Python."""
    if os.environ.get("EXLIBRIS_REEXEC") == "1":
        return
    try:
        import sqlalchemy  # noqa: F401
    except ModuleNotFoundError:
        if _VENV_PYTHON.is_file():
            os.environ["EXLIBRIS_REEXEC"] = "1"
            os.execv(str(_VENV_PYTHON), [str(_VENV_PYTHON), *sys.argv])
        print(
            "error: scanner dependencies are not installed.\n"
            "  python3 -m venv .venv && .venv/bin/pip install -e .\n"
            "  or: source .venv/bin/activate && python scan_books.py",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


_ensure_project_python()

import argparse

from exlibris.config import load_settings, resolve_covers_dir, resolve_database_path
from exlibris.database import get_engine, init_db
from exlibris.ebook_meta import EbookMetaError, find_ebook_meta
from exlibris.scanner import print_scan_progress, scan_paths


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Recursively scan directories for ebooks and store metadata "
            "in the ExLibris SQLite database via Calibre's ebook-meta."
        )
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Directory trees to scan (default: /media/books from config or settings)",
    )
    parser.add_argument(
        "--database",
        "-d",
        type=Path,
        default=None,
        help="SQLite database path (default: data/library.db or config.json value)",
    )
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        default=None,
        help="Optional config.json for database_path and scan_paths defaults",
    )
    parser.add_argument(
        "--ebook-meta",
        type=str,
        default=None,
        help="Path to the ebook-meta executable (default: search PATH)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print non-fatal metadata extraction warnings",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress per-file progress lines",
    )
    return parser.parse_args(argv)


def _format_summary(stats) -> str:
    parts = [
        f"Scanned {stats.scanned} file(s)",
        f"added or updated {stats.added_or_updated} record(s)",
    ]
    if stats.skipped:
        parts.append(f"skipped {stats.skipped} duplicate(s)")
    if stats.unchanged:
        parts.append(f"skipped {stats.unchanged} unchanged")
    if stats.marked_missing:
        parts.append(f"marked {stats.marked_missing} missing")
    if stats.invalid_epubs:
        parts.append(f"skipped {stats.invalid_epubs} invalid EPUB(s)")
    if stats.files_deleted:
        parts.append(f"deleted {stats.files_deleted} file(s)")
    return "; ".join(parts)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    from exlibris.job_lock import LibraryJobLockedError, library_job_lock

    try:
        with library_job_lock(job_name="library scan"):
            return _run_scan(args)
    except LibraryJobLockedError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _run_scan(args) -> int:
    settings = load_settings(args.config.expanduser() if args.config else None)

    scan_targets = [p.expanduser() for p in args.paths] if args.paths else settings.scan_paths
    if not scan_targets:
        print("error: no scan paths configured", file=sys.stderr)
        return 1

    try:
        ebook_meta_cmd = find_ebook_meta(args.ebook_meta)
    except EbookMetaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    db_path = resolve_database_path(
        args.database.expanduser() if args.database else settings.database_path
    )
    engine = get_engine(db_path)
    SessionLocal = init_db(engine)

    progress = None if args.quiet else print_scan_progress

    with SessionLocal() as session:
        stats = scan_paths(
            session,
            scan_targets,
            ebook_meta_cmd=ebook_meta_cmd,
            covers_dir=resolve_covers_dir(settings.covers_dir),
            verbose=args.verbose,
            on_progress=progress,
            validate_epub=True,
        )

    print(f"{_format_summary(stats)} in {db_path}.")
    if stats.errors:
        print(f"{len(stats.errors)} issue(s):", file=sys.stderr)
        for err in stats.errors:
            print(f"  - {err}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
