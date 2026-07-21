"""Calibre ebook-convert wrapper for EPUB normalization."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

from exlibris.config import PROJECT_ROOT


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


# Flags that preserve a raster cover when normalizing to EPUB 2. Without
# --no-svg-cover, Calibre may keep an SVG cover that EPUB 2 readers and
# ebook-meta --get-cover treat as missing (blank cover page).
EPUB2_CONVERT_OPTIONS = (
    "--epub-version=2",
    "--prefer-metadata-cover",
    "--no-svg-cover",
    "--preserve-cover-aspect-ratio",
)


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
        [cmd, str(source), str(dest), *EPUB2_CONVERT_OPTIONS],
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


_convert_epub2_module = None


def _load_convert_epub2():
    """Load the standalone convert_epub2.py script as a module."""
    global _convert_epub2_module
    if _convert_epub2_module is not None:
        return _convert_epub2_module

    script = PROJECT_ROOT / "convert_epub2.py"
    spec = importlib.util.spec_from_file_location("convert_epub2", script)
    if spec is None or spec.loader is None:
        raise FileNotFoundError(f"convert_epub2.py not found at {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _convert_epub2_module = module
    return module


def convert_epub2_in_place(
    file_path: Path,
    *,
    ebook_meta_cmd: str | None = None,
    ebook_convert_cmd: str | None = None,
    verbose: bool = False,
) -> bool:
    """Run convert_epub2.py's in-place EPUB 2 conversion on one file."""
    try:
        module = _load_convert_epub2()
        convert_cmd = module.find_tool("ebook-convert", ebook_convert_cmd)
        meta_cmd = module.find_tool("ebook-meta", ebook_meta_cmd)
    except FileNotFoundError as exc:
        if verbose:
            print(f"convert_epub2 unavailable: {exc}", flush=True)
        return False
    return bool(
        module.convert_in_place(
            file_path,
            convert_cmd=convert_cmd,
            meta_cmd=meta_cmd,
            dry_run=False,
            verbose=verbose,
        )
    )
