#!/usr/bin/env python3
"""Convert EPUB(s) to EPUB 2 in place using Calibre ebook-convert."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

EPUB2_OPTIONS = (
    "--epub-version=2",
    "--no-svg-cover",
)
EPUB_MODE = 0o644


def find_tool(name: str, explicit: str | None) -> str:
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"{name} not found: {path}")
        return str(path.resolve())
    found = shutil.which(name)
    if not found:
        raise FileNotFoundError(
            f"{name} not found on PATH (install Calibre or pass --{name.replace('-', '-')})"
        )
    return found


def iter_epubs(target: Path) -> list[Path]:
    target = target.expanduser().resolve()
    if target.is_file():
        if target.suffix.lower() != ".epub":
            raise ValueError(f"not an EPUB file: {target}")
        return [target]
    if not target.is_dir():
        raise FileNotFoundError(f"not found: {target}")
    return sorted(
        path.resolve()
        for path in target.rglob("*")
        if path.is_file() and path.suffix.lower() == ".epub"
    )


def extract_cover(epub: Path, dest: Path, *, ebook_meta_cmd: str) -> bool:
    result = subprocess.run(
        [ebook_meta_cmd, str(epub), "--get-cover", str(dest)],
        capture_output=True,
        text=True,
        check=False,
    )
    return (
        result.returncode == 0
        and dest.is_file()
        and dest.stat().st_size > 0
    )


def convert_in_place(
    epub: Path,
    *,
    convert_cmd: str,
    meta_cmd: str,
    dry_run: bool,
    verbose: bool,
) -> bool:
    if dry_run:
        print(f"would convert: {epub}")
        return True

    temp_epub: Path | None = None
    temp_cover: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".epub",
            dir=epub.parent,
            delete=False,
        ) as handle:
            temp_epub = Path(handle.name)

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as cover_handle:
            temp_cover = Path(cover_handle.name)

        options = list(EPUB2_OPTIONS)
        if extract_cover(epub, temp_cover, ebook_meta_cmd=meta_cmd):
            options.extend(["--cover", str(temp_cover)])
        elif verbose:
            print(f"no cover extracted: {epub}", file=sys.stderr)

        result = subprocess.run(
            [convert_cmd, str(epub), str(temp_epub), *options],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(detail or f"exit code {result.returncode}")

        if not temp_epub.is_file() or temp_epub.stat().st_size == 0:
            raise RuntimeError("ebook-convert produced no output")

        os.replace(temp_epub, epub)
        temp_epub = None
        os.chmod(epub, EPUB_MODE)

        if verbose:
            print(f"converted: {epub}")
        return True
    except (OSError, RuntimeError) as exc:
        print(f"failed: {epub}: {exc}", file=sys.stderr)
        return False
    finally:
        for path in (temp_epub, temp_cover):
            if path is not None and path.is_file():
                try:
                    path.unlink()
                except OSError:
                    pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert one EPUB file or all EPUBs under a directory to EPUB 2 in place."
    )
    parser.add_argument("target", type=Path, help="EPUB file or directory")
    parser.add_argument("--ebook-convert", default=None, help="Path to ebook-convert")
    parser.add_argument("--ebook-meta", default=None, help="Path to ebook-meta")
    parser.add_argument("-n", "--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    try:
        convert_cmd = find_tool("ebook-convert", args.ebook_convert)
        meta_cmd = find_tool("ebook-meta", args.ebook_meta)
        epubs = iter_epubs(args.target)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not epubs:
        print(f"No EPUB files under {args.target.expanduser().resolve()}")
        return 0

    ok = failed = 0
    total = len(epubs)
    for index, epub in enumerate(epubs, start=1):
        if not args.verbose and not args.dry_run and total > 1:
            width = len(str(total))
            print(f"[{index:>{width}}/{total}] {epub.name}", flush=True)
        if convert_in_place(
            epub,
            convert_cmd=convert_cmd,
            meta_cmd=meta_cmd,
            dry_run=args.dry_run,
            verbose=args.verbose,
        ):
            ok += 1
        else:
            failed += 1

    verb = "would convert" if args.dry_run else "converted"
    print(f"{verb} {ok} EPUB(s); failed {failed} of {total}.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
