from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from exlibris.config import PROJECT_ROOT, resolve_covers_dir, resolve_scan_path
from exlibris.database import find_book_by_content_hash, upsert_book
from exlibris.ebook_meta import EbookMetaError, extract_cover, read_metadata
from exlibris.file_hash import sha1_file
from exlibris.models import Book

SUPPORTED_EXTENSIONS = {".epub"}

ScanProgressCallback = Callable[[int, int, Path, str], None]


@dataclass
class ScanStats:
    scanned: int = 0
    added_or_updated: int = 0
    skipped: int = 0
    unchanged: int = 0
    marked_missing: int = 0
    errors: list[str] | None = None

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


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
) -> tuple[list[Path], list[Path], list[str]]:
    """Gather ebook paths from scan roots. Returns (files, scanned_roots, errors)."""
    files: list[Path] = []
    scanned_roots: list[Path] = []
    errors: list[str] = []
    for root in paths:
        root = resolve_scan_path(root)
        if not root.exists():
            errors.append(f"path not found: {root}")
            continue
        scanned_roots.append(root)
        files.extend(iter_book_files(root))
    return files, scanned_roots, errors


def _books_under_root(root: Path):
    """Books indexed under a scan root (exact path or nested files)."""
    root_str = str(root)
    return select(Book).where(
        Book.is_missing.is_(False),
        or_(Book.file_path == root_str, Book.file_path.like(f"{root_str}/%")),
    )


def mark_missing_books(
    session: Session,
    scanned_roots: list[Path],
    seen_paths: set[str],
) -> int:
    """Mark indexed books missing when absent from a completed scan of their root."""
    marked = 0
    for root in scanned_roots:
        for book in session.scalars(_books_under_root(root)):
            if book.file_path not in seen_paths:
                book.is_missing = True
                marked += 1
    if marked:
        session.commit()
    return marked


def _mark_file_present(session: Session, existing: Book | None) -> bool:
    """Clear is_missing when a file is seen on disk again (e.g. remounted volume)."""
    if existing is not None and existing.is_missing:
        existing.is_missing = False
        session.add(existing)
        return True
    return False


def _skip_reason_before_calibre(
    session: Session,
    *,
    existing: Book | None,
    content_hash: str,
) -> str | None:
    """Return a skip reason when Calibre should not run (hash checks only)."""
    canonical = find_book_by_content_hash(session, content_hash)

    if canonical is not None and (
        existing is None or canonical.id != existing.id
    ):
        return "duplicate"

    if (
        existing is not None
        and existing.content_hash == content_hash
    ):
        return "unchanged"

    return None


def _unchanged_by_stat(existing: Book, *, file_size: int, file_mtime: float) -> bool:
    """True when the file at this path matches stored size and mtime (no hash read)."""
    return (
        existing.file_size == file_size
        and existing.file_mtime == file_mtime
    )


def print_scan_progress(current: int, total: int, path: Path, status: str) -> None:
    width = len(str(total))
    print(f"[{current:>{width}}/{total}] {status}: {path.name}", flush=True)


def scan_paths(
    session: Session,
    paths: list[Path],
    *,
    ebook_meta_cmd: str | None = None,
    covers_dir: Path | None = None,
    verbose: bool = False,
    on_progress: ScanProgressCallback | None = None,
) -> ScanStats:
    stats = ScanStats()
    now = datetime.now(timezone.utc)
    covers_root = resolve_covers_dir(covers_dir)

    book_files, scanned_roots, path_errors = collect_book_files(paths)
    stats.errors.extend(path_errors)
    total = len(book_files)
    seen_paths: set[str] = set()

    if on_progress and total:
        print(f"Scanning {total:,} ebook file(s)...", flush=True)

    for index, file_path in enumerate(book_files, start=1):
        stats.scanned += 1
        try:
            stat = file_path.stat()
            file_path_str = str(file_path)
            seen_paths.add(file_path_str)

            existing = session.scalar(
                select(Book).where(Book.file_path == file_path_str)
            )
            if _mark_file_present(session, existing):
                session.commit()

            if existing is not None and _unchanged_by_stat(
                existing,
                file_size=stat.st_size,
                file_mtime=stat.st_mtime,
            ):
                stats.unchanged += 1
                if on_progress:
                    on_progress(index, total, file_path, "unchanged")
                elif verbose:
                    print(f"unchanged: {file_path.name}", flush=True)
                continue

            content_hash = sha1_file(file_path)
            skip_reason = _skip_reason_before_calibre(
                session,
                existing=existing,
                content_hash=content_hash,
            )
            if skip_reason == "duplicate":
                stats.skipped += 1
                if on_progress:
                    on_progress(index, total, file_path, "duplicate")
                elif verbose:
                    canonical = find_book_by_content_hash(session, content_hash)
                    print(
                        f"duplicate: {file_path.name} "
                        f"(same as {canonical.file_path if canonical else 'unknown'})",
                        flush=True,
                    )
                continue

            if skip_reason == "unchanged":
                stats.unchanged += 1
                if on_progress:
                    on_progress(index, total, file_path, "unchanged")
                elif verbose:
                    print(f"unchanged: {file_path.name}", flush=True)
                continue

            meta = read_metadata(file_path, ebook_meta_cmd=ebook_meta_cmd)
            if meta.errors and verbose:
                stats.errors.extend(f"{file_path}: {err}" for err in meta.errors)

            book = upsert_book(
                session,
                {
                    "file_path": file_path_str,
                    "file_name": file_path.name,
                    "format": meta.format or file_path.suffix.lower().lstrip("."),
                    "file_size": stat.st_size,
                    "file_mtime": stat.st_mtime,
                    "content_hash": content_hash,
                    "title": meta.title or file_path.stem,
                    "sort_title": meta.sort_title,
                    "authors": meta.authors,
                    "publisher": meta.publisher,
                    "published_date": meta.published_date,
                    "isbn": meta.isbn,
                    "language": meta.language,
                    "description": meta.description,
                    "series": meta.series,
                    "series_index": meta.series_index,
                    "tags": meta.tags,
                    "last_scanned_at": now,
                },
            )
            session.flush()

            cover_file = extract_cover(
                file_path,
                covers_root / str(book.id),
                ebook_meta_cmd=ebook_meta_cmd,
            )
            if cover_file:
                book.cover_path = str(cover_file.relative_to(PROJECT_ROOT))
                session.add(book)

            session.commit()
            stats.added_or_updated += 1
            if on_progress:
                on_progress(index, total, file_path, "indexed")
            elif verbose:
                title = book.title or book.file_name
                print(f"indexed: {title}", flush=True)
        except EbookMetaError as exc:
            session.rollback()
            stats.errors.append(f"{file_path}: {exc}")
            if on_progress:
                on_progress(index, total, file_path, "error")
        except Exception as exc:
            session.rollback()
            stats.errors.append(f"{file_path}: {exc}")
            if on_progress:
                on_progress(index, total, file_path, "error")

    if scanned_roots:
        stats.marked_missing = mark_missing_books(
            session, scanned_roots, seen_paths
        )
        if stats.marked_missing and verbose:
            print(
                f"marked {stats.marked_missing} book(s) missing",
                flush=True,
            )

    return stats
