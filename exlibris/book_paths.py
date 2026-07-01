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


def path_keeper_key(path: Path) -> tuple[int, int, str]:
    resolved = path.resolve()
    return (len(resolved.name), len(str(resolved)), str(resolved))


def keeper_path(candidates: list[Path]) -> Path:
    """Prefer the path with the longest basename, then longest full path."""
    if not candidates:
        raise ValueError("keeper_path requires at least one candidate")
    return max(candidates, key=path_keeper_key)


def is_path_under_root(path: Path, root: Path) -> bool:
    path = path.resolve()
    root = root.resolve()
    return path == root or root in path.parents


def path_is_under_any_root(path: Path, roots: list[Path]) -> bool:
    return any(is_path_under_root(path, root) for root in roots)


def delete_file_under_roots(path: Path, scan_roots: list[Path]) -> None:
    """Delete a file only when it lies under a configured scan root."""
    path = path.resolve()
    if not path_is_under_any_root(path, scan_roots):
        raise OSError(f"refusing to delete outside scan roots: {path}")
    path.unlink()


def prune_empty_directories(
    scan_roots: list[Path],
    *,
    execute: bool,
) -> int:
    """Remove empty directories under scan roots (bottom-up). Never removes roots."""
    removed = 0
    for root in scan_roots:
        root = root.resolve()
        if not root.is_dir():
            continue
        directories = [
            path
            for path in root.rglob("*")
            if path.is_dir() and path.resolve() != root
        ]
        for dirpath in sorted(directories, key=lambda path: len(path.parts), reverse=True):
            try:
                if any(dirpath.iterdir()):
                    continue
                if execute:
                    dirpath.rmdir()
                removed += 1
            except OSError:
                continue
    return removed
