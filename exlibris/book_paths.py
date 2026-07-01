from __future__ import annotations

from pathlib import Path

SUPPORTED_EXTENSIONS = {".epub"}


def iter_book_files(root: Path):
    if not root.exists():
        return
    if root.is_file():
        if root.suffix.lower() in SUPPORTED_EXTENSIONS:
            yield root.resolve()
        return
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            yield path.resolve()


def collect_book_files(
    paths: list[Path],
    *,
    resolve_path,
) -> tuple[list[Path], list[Path], list[str]]:
    """Gather ebook paths from scan roots. Returns (files, scanned_roots, errors)."""
    files: list[Path] = []
    scanned_roots: list[Path] = []
    errors: list[str] = []
    for root in paths:
        root = resolve_path(root)
        if not root.exists():
            errors.append(f"path not found: {root}")
            continue
        scanned_roots.append(root)
        files.extend(iter_book_files(root))
    return files, scanned_roots, errors
