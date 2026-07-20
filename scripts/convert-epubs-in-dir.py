#!/usr/bin/env python3
"""Convert every EPUB under a directory to EPUB 2 in place (no database updates)."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from exlibris.ebook_convert import EbookConvertError, convert_epub_to_version2

EPUB_FILE_MODE = 0o644


def _iter_epubs(root: Path) -> list[Path]:
    return sorted(
        path.resolve()
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() == ".epub"
    )


def _convert_in_place(
    path: Path,
    *,
    ebook_convert_cmd: str | None,
    dry_run: bool,
    verbose: bool,
) -> bool:
    if dry_run:
        print(f"would convert: {path}")
        return True

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".epub",
            dir=path.parent,
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
        convert_epub_to_version2(
            path,
            temp_path,
            ebook_convert_cmd=ebook_convert_cmd,
        )
        os.replace(temp_path, path)
        temp_path = None
        os.chmod(path, EPUB_FILE_MODE)
        if verbose:
            print(f"converted: {path}")
        return True
    except (EbookConvertError, OSError) as exc:
        if temp_path is not None and temp_path.is_file():
            try:
                temp_path.unlink()
            except OSError:
                pass
        print(f"failed: {path}: {exc}", file=sys.stderr)
        return False


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Re-encode every EPUB under a directory to EPUB 2 with Calibre "
            "ebook-convert. Files are replaced in place; library.db is not updated. "
            "Prefer update_epubs.py for indexed libraries."
        )
    )
    parser.add_argument(
        "directory",
        type=Path,
        help="Root directory to scan recursively for .epub files",
    )
    parser.add_argument(
        "--ebook-convert",
        type=str,
        default=None,
        help="Path to Calibre ebook-convert (default: search PATH)",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="List EPUBs that would be converted without changing files",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Log each successful conversion",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.directory.expanduser().resolve()
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 1

    epubs = _iter_epubs(root)
    if not epubs:
        print(f"No EPUB files under {root}")
        return 0

    converted = 0
    failed = 0
    for index, path in enumerate(epubs, start=1):
        if not args.verbose and not args.dry_run:
            width = len(str(len(epubs)))
            print(f"[{index:>{width}}/{len(epubs)}] {path.name}", flush=True)
        if _convert_in_place(
            path,
            ebook_convert_cmd=args.ebook_convert,
            dry_run=args.dry_run,
            verbose=args.verbose or args.dry_run,
        ):
            converted += 1
        else:
            failed += 1

    verb = "would convert" if args.dry_run else "converted"
    print(f"{verb} {converted} EPUB(s); failed {failed} of {len(epubs)}.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
