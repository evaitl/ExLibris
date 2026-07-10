#!/usr/bin/env python3
"""Re-encode indexed EPUBs to EPUB 2 using Calibre ebook-convert."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_VENV_PYTHON = _ROOT / ".venv" / "bin" / "python"


def _ensure_project_python() -> None:
    """Re-exec with the project venv when SQLAlchemy is not installed."""
    if os.environ.get("EXLIBRIS_REEXEC") == "1":
        return
    try:
        import sqlalchemy  # noqa: F401
    except ModuleNotFoundError:
        if _VENV_PYTHON.is_file():
            os.environ["EXLIBRIS_REEXEC"] = "1"
            os.execv(str(_VENV_PYTHON), [str(_VENV_PYTHON), *sys.argv])
        print(
            "error: update_epubs requires the project virtualenv.\n"
            "  python3 -m venv .venv && .venv/bin/pip install -e .\n"
            "  or: source .venv/bin/activate && python update_epubs.py",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


_ensure_project_python()

from exlibris.config import load_settings, resolve_covers_dir, resolve_database_path, resolve_scan_path
from exlibris.database import get_engine, init_db
from exlibris.ebook_convert import EbookConvertError, find_ebook_convert
from exlibris.epub_update import SUCCESS_LOG_INTERVAL, update_epubs
from exlibris.sqlite_retry import configure_sqlite_connection


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert indexed EPUBs to EPUB 2 with Calibre ebook-convert, "
            "update file hashes in the database, and remove books that fail conversion."
        )
    )
    parser.add_argument(
        "--database",
        "-d",
        type=Path,
        default=None,
        help="SQLite database path (default: data/library.db or config.json)",
    )
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        default=None,
        help="Path to config.json",
    )
    parser.add_argument(
        "--path",
        "-p",
        type=Path,
        action="append",
        default=None,
        help="Scan root limiting which indexed EPUBs are processed (repeatable)",
    )
    parser.add_argument(
        "--ebook-convert",
        type=str,
        default=None,
        help="Path to Calibre ebook-convert (default: search PATH)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply conversions and database updates (default is dry-run)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Log every EPUB as it is converted (or would be in dry-run)",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress per-file progress",
    )
    return parser.parse_args(argv)


def _update_log_callbacks(*, quiet: bool, verbose: bool):
    if quiet:
        return None, None

    def on_start(candidates: int, skipped_converted: int) -> None:
        if skipped_converted:
            print(f"Skipping {skipped_converted} already converted EPUB(s)", flush=True)
        if candidates:
            print(f"Processing {candidates} EPUB(s)...", flush=True)

    def on_event(current: int, total: int, name: str, event: str, detail: str) -> None:
        if total <= 0:
            return
        width = len(str(total))
        prefix = f"[{current:>{width}}/{total}]"
        if event in ("would_convert", "converted"):
            if (
                not verbose
                and current != 1
                and current != total
                and current % SUCCESS_LOG_INTERVAL != 0
            ):
                return
            label = "would convert" if event == "would_convert" else "converted"
            print(f"{prefix} {label}: {name}", flush=True)
        elif event == "removed":
            message = f"{prefix} removed: {name}"
            if detail:
                message = f"{message}: {detail}"
            print(message, file=sys.stderr, flush=True)
        elif event == "missing":
            print(f"{prefix} missing: {name}", file=sys.stderr, flush=True)

    return on_start, on_event


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    from exlibris.job_lock import LibraryJobLockedError, library_job_lock

    try:
        with library_job_lock(job_name="EPUB update"):
            return _run_update(args)
    except LibraryJobLockedError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _run_update(args: argparse.Namespace) -> int:
    settings = load_settings(args.config.expanduser() if args.config else None)

    try:
        find_ebook_convert(args.ebook_convert)
    except EbookConvertError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    db_path = resolve_database_path(
        args.database.expanduser() if args.database else settings.database_path
    )
    covers_dir = resolve_covers_dir(settings.covers_dir)
    if args.path:
        scan_roots = [resolve_scan_path(path.expanduser()) for path in args.path]
    else:
        scan_roots = [resolve_scan_path(path) for path in settings.scan_paths]

    execute = args.execute
    if not args.quiet:
        mode = "EXECUTE" if execute else "DRY-RUN"
        print(f"=== {mode} ===")
        print(f"Database: {db_path}")
        print(f"Scan roots: {', '.join(str(root) for root in scan_roots)}")

    engine = get_engine(db_path)
    init_db(engine)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    configure_sqlite_connection(conn)
    on_start, on_event = _update_log_callbacks(quiet=args.quiet, verbose=args.verbose)
    try:
        stats = update_epubs(
            conn,
            scan_roots,
            covers_dir,
            execute=execute,
            ebook_convert_cmd=args.ebook_convert,
            on_start=on_start,
            on_event=on_event,
        )
    finally:
        conn.close()

    verb = "would convert" if not execute else "converted"
    removed_verb = "would remove" if not execute else "removed"
    print(
        f"{verb} {stats.converted} EPUB(s); "
        f"{removed_verb} {stats.removed} failed; "
        f"skipped {stats.skipped_converted} already converted, "
        f"{stats.skipped_missing} missing on disk "
        f"({stats.candidates} candidate(s))."
    )
    if not execute:
        print("Re-run with --execute to apply changes.")

    if stats.errors and not args.quiet:
        print(
            f"{len(stats.errors)} conversion error(s) logged above.",
            file=sys.stderr,
        )
    return 1 if stats.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
