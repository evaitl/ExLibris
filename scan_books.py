#!/usr/bin/env python3
"""Walk directory trees and populate the ExLibris database using ebook-meta."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from exlibris.config import load_settings
from exlibris.database import get_engine, init_db
from exlibris.ebook_meta import EbookMetaError, find_ebook_meta
from exlibris.scanner import scan_paths


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
        help="Directory trees to scan (default: books/ from config or settings)",
    )
    parser.add_argument(
        "--database",
        "-d",
        type=Path,
        default=None,
        help="SQLite database path (default: library.db or config.yaml value)",
    )
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        default=None,
        help="Optional config.yaml for database_path and scan_paths defaults",
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
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

    db_path = args.database.expanduser() if args.database else settings.database_path
    engine = get_engine(db_path)
    SessionLocal = init_db(engine)

    with SessionLocal() as session:
        stats = scan_paths(
            session,
            scan_targets,
            ebook_meta_cmd=ebook_meta_cmd,
            covers_dir=settings.covers_dir,
            verbose=args.verbose,
        )

    print(
        f"Scanned {stats.scanned} file(s); "
        f"added or updated {stats.added_or_updated} record(s) in {db_path}."
    )
    if stats.errors:
        print(f"{len(stats.errors)} issue(s):", file=sys.stderr)
        for err in stats.errors:
            print(f"  - {err}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
