#!/usr/bin/env python3
"""Sample EPUB text and fill ExLibris Genre fields."""

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
            "error: classifier dependencies are not installed.\n"
            "  python3 -m venv .venv && .venv/bin/pip install -e .\n"
            "  or: source .venv/bin/activate && python classify.py",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


_ensure_project_python()

import argparse

from exlibris.classify import DEFAULT_ADULT_THRESHOLD
from exlibris.classify_job import classify_library, open_library_connection
from exlibris.config import load_settings, resolve_database_path
from exlibris.database import DatabaseNotWritableError, get_engine, init_db


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Sample indexed EPUB body text and fill the Genre field with up to "
            "three closed-set labels. Default is a dry run; pass --execute to write."
        )
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
        help="Only classify books under this directory or file (repeatable)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of books to examine",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Write genre values (default is a dry run)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Reclassify books with automatic genre values",
    )
    parser.add_argument(
        "--overwrite-manual",
        action="store_true",
        help="Also replace genres that were edited by an administrator",
    )
    parser.add_argument(
        "--adult-threshold",
        type=float,
        default=None,
        help=(
            "Explicit-term hits per 1,000 words that add Erotica "
            f"(default: {DEFAULT_ADULT_THRESHOLD:g})"
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print a line for every book as it is classified",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress per-book sample lines",
    )
    return parser.parse_args(argv)


def _print_stats(stats, *, execute: bool, verbose: bool) -> None:
    mode = "Wrote" if execute else "Dry run"
    print(
        f"{mode}: examined {stats.examined}, classified {stats.classified}, "
        f"skipped {stats.skipped}, empty {stats.empty}, failed {stats.failed}."
    )
    if stats.histogram:
        print("Genre counts:")
        for label, count in stats.histogram.most_common():
            print(f"  {label}: {count}")
    if not verbose and stats.samples:
        print("Sample:")
        for line in stats.samples[:15]:
            print(f"  {line}")
    if stats.errors:
        print(f"{len(stats.errors)} issue(s):", file=sys.stderr)
        for err in stats.errors:
            print(f"  - {err}", file=sys.stderr)
    if not execute:
        print("Re-run with --execute to write genre values.")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    from exlibris.job_lock import LibraryJobLockedError, library_job_lock

    threshold = (
        DEFAULT_ADULT_THRESHOLD if args.adult_threshold is None else args.adult_threshold
    )
    if threshold < 0:
        print("error: --adult-threshold must be non-negative", file=sys.stderr)
        return 1

    settings = load_settings(args.config.expanduser() if args.config else None)
    db_path = resolve_database_path(settings.database_path)
    path_filters = [p.expanduser() for p in args.path] if args.path else None
    on_progress = None if args.quiet else (print if args.verbose else None)

    try:
        with library_job_lock(job_name="library classify"):
            engine = get_engine(db_path)
            init_db(engine)
            engine.dispose()
            conn = open_library_connection(db_path)
            try:
                stats = classify_library(
                    conn,
                    execute=args.execute,
                    overwrite=args.overwrite,
                    overwrite_manual=args.overwrite_manual,
                    adult_threshold=threshold,
                    path_filters=path_filters,
                    limit=args.limit,
                    on_progress=on_progress,
                )
            finally:
                conn.close()
    except LibraryJobLockedError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except DatabaseNotWritableError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    _print_stats(stats, execute=args.execute, verbose=args.verbose)
    return 1 if stats.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
