"""Calibre ebook-convert wrapper for EPUB normalization."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class EbookConvertError(Exception):
    pass


def find_ebook_convert(explicit: str | None = None) -> str:
    if explicit:
        path = Path(explicit).expanduser()
        if not path.exists():
            raise EbookConvertError(f"ebook-convert not found at {path}")
        return str(path.resolve())
    found = shutil.which("ebook-convert")
    if not found:
        raise EbookConvertError(
            "ebook-convert not found on PATH (install Calibre or pass --ebook-convert)"
        )
    return found


def convert_epub_to_version2(
    source: Path,
    dest: Path,
    *,
    ebook_convert_cmd: str | None = None,
) -> None:
    """Write an EPUB 2 file to ``dest`` from ``source`` using Calibre."""
    source = source.expanduser().resolve()
    dest = dest.expanduser().resolve()
    cmd = find_ebook_convert(ebook_convert_cmd)
    result = subprocess.run(
        [cmd, str(source), str(dest), "--epub-version=2"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise EbookConvertError(
            detail or f"ebook-convert exited with {result.returncode}"
        )
    if not dest.is_file() or dest.stat().st_size == 0:
        raise EbookConvertError("ebook-convert produced no output file")
