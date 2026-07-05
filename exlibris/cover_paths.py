"""Cover file layout and URL helpers (sharded under data/covers/)."""

from __future__ import annotations

from pathlib import Path

COVER_EXTENSIONS = (".jpg", ".jpeg", ".png")


def cover_shard(book_id: int) -> str:
    """Two-digit shard from the last two digits of the numeric basename."""
    return f"{book_id % 100:02d}"


def cover_storage_path(covers_root: Path, book_id: int, suffix: str) -> Path:
    ext = suffix if suffix.startswith(".") else f".{suffix}"
    return covers_root / cover_shard(book_id) / f"{book_id}{ext.lower()}"


def cover_dest_base(covers_root: Path, book_id: int) -> Path:
    """Path without extension for extract_cover dest_base."""
    return covers_root / cover_shard(book_id) / str(book_id)


def cover_relative_path(
    covers_root: Path,
    book_id: int,
    suffix: str,
    *,
    project_root: Path,
) -> str:
    storage = cover_storage_path(covers_root, book_id, suffix)
    try:
        return str(storage.relative_to(project_root))
    except ValueError:
        shard_path = f"{cover_shard(book_id)}/{book_id}{suffix.lower()}"
        return f"data/covers/{shard_path}"


def cover_public_segment(cover_path: str) -> str:
    """URL path under the covers static prefix (e.g. 42/12342.jpg)."""
    normalized = cover_path.replace("\\", "/")
    for prefix in ("data/covers/", "covers/"):
        if normalized.startswith(prefix):
            return normalized[len(prefix) :]
    return normalized


def parse_book_id_from_cover(path: Path) -> int | None:
    try:
        return int(path.stem)
    except ValueError:
        return None


def is_flat_cover(path: Path, covers_root: Path) -> bool:
    return path.parent.resolve() == covers_root.resolve()


def iter_cover_files(covers_dir: Path):
    if not covers_dir.is_dir():
        return
    for path in covers_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in COVER_EXTENSIONS:
            yield path


def legacy_cover_paths(covers_root: Path, book_id: int) -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()
    for ext in COVER_EXTENSIONS:
        for path in (
            covers_root / f"{book_id}{ext}",
            cover_storage_path(covers_root, book_id, ext),
        ):
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                paths.append(path)
    return paths


def remove_cover_files(covers_root: Path, book_id: int) -> None:
    for path in legacy_cover_paths(covers_root, book_id):
        if path.is_file():
            path.unlink()
