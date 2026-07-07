from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from exlibris.author_tokens import sync_author_tokens
from exlibris.book_paths import (
    collect_book_files,
    delete_file_under_roots,
    path_keeper_key,
    path_is_under_any_root,
)
from exlibris.config import PROJECT_ROOT, resolve_covers_dir, resolve_scan_path
from exlibris.cover_paths import cover_dest_base
from exlibris.database import find_book_by_content_hash, upsert_book
from exlibris.ebook_meta import EbookMetaError, extract_cover, read_metadata
from exlibris.file_hash import sha1_file
from exlibris.library_cache import refresh_library_stats
from exlibris.filenames import display_name, display_path, display_text, ensure_safe_filename
from exlibris.models import Book

ScanProgressCallback = Callable[[int, int, Path, str], None]


@dataclass
class ScanStats:
    scanned: int = 0
    added_or_updated: int = 0
    skipped: int = 0
    unchanged: int = 0
    marked_missing: int = 0
    files_deleted: int = 0
    errors: list[str] | None = None

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


def collect_book_files_from_config(
    paths: list[Path],
) -> tuple[list[Path], list[Path], list[str]]:
    return collect_book_files(paths, resolve_path=resolve_scan_path)


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


def _repoint_book_to_file(
    book: Book,
    file_path: Path,
    *,
    file_size: int,
    file_mtime: float,
) -> None:
    """Update file identity fields only; bibliographic metadata is unchanged."""
    book.file_path = str(file_path)
    book.file_name = file_path.name
    book.file_size = file_size
    book.file_mtime = file_mtime
    book.is_missing = False


def _should_repoint_canonical_to_path(canonical: Book, file_path: Path) -> bool:
    old_path = Path(canonical.file_path)
    if not old_path.is_file() or canonical.is_missing:
        return True
    return path_keeper_key(file_path) > path_keeper_key(old_path)


def _try_repoint_duplicate(
    session: Session,
    *,
    file_path: Path,
    file_size: int,
    file_mtime: float,
    content_hash: str,
    existing: Book | None,
    scan_roots: list[Path] | None = None,
) -> FileScanResult | None:
    """Repoint canonical row when content matches and this path should be keeper."""
    canonical = find_book_by_content_hash(session, content_hash)
    if canonical is None:
        return None
    if existing is not None and canonical.id == existing.id:
        return None
    if not _should_repoint_canonical_to_path(canonical, file_path):
        return None
    old_path = Path(canonical.file_path).resolve()
    new_path = file_path.resolve()
    _repoint_book_to_file(
        canonical,
        file_path,
        file_size=file_size,
        file_mtime=file_mtime,
    )
    session.add(canonical)
    session.commit()

    files_deleted = 0
    if (
        scan_roots
        and old_path != new_path
        and old_path.is_file()
        and path_is_under_any_root(old_path, scan_roots)
    ):
        delete_file_under_roots(old_path, scan_roots)
        files_deleted = 1

    return FileScanResult("repointed", canonical, files_deleted=files_deleted)


def print_scan_progress(current: int, total: int, path: Path, status: str) -> None:
    width = len(str(total))
    print(f"[{current:>{width}}/{total}] {status}: {display_name(path)}", flush=True)


@dataclass
class FileScanResult:
    status: str
    book: Book | None = None
    files_deleted: int = 0


def scan_single_file(
    session: Session,
    file_path: Path,
    *,
    ebook_meta_cmd: str | None = None,
    covers_dir: Path | None = None,
    now: datetime | None = None,
    verbose: bool = False,
    scan_roots: list[Path] | None = None,
) -> FileScanResult:
    """Index one ebook file. Returns status: indexed, unchanged, duplicate, or error."""
    if now is None:
        now = datetime.now(timezone.utc)
    covers_root = resolve_covers_dir(covers_dir)
    file_path = ensure_safe_filename(file_path.resolve())

    try:
        stat = file_path.stat()
        file_path_str = str(file_path)

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
            return FileScanResult("unchanged", existing)

        content_hash = sha1_file(file_path)
        skip_reason = _skip_reason_before_calibre(
            session,
            existing=existing,
            content_hash=content_hash,
        )
        if skip_reason == "duplicate":
            repointed = _try_repoint_duplicate(
                session,
                file_path=file_path,
                file_size=stat.st_size,
                file_mtime=stat.st_mtime,
                content_hash=content_hash,
                existing=existing,
                scan_roots=scan_roots,
            )
            if repointed is not None:
                return repointed
            return FileScanResult("duplicate", existing)

        if skip_reason == "unchanged":
            return FileScanResult("unchanged", existing)

        meta = read_metadata(file_path, ebook_meta_cmd=ebook_meta_cmd)
        if meta.errors and verbose:
            for err in meta.errors:
                print(f"{display_path(file_path)}: {err}", flush=True)

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
            cover_dest_base(covers_root, book.id),
            ebook_meta_cmd=ebook_meta_cmd,
        )
        if cover_file:
            book.cover_path = str(cover_file.relative_to(PROJECT_ROOT))
            session.add(book)

        session.commit()
        raw = session.connection().connection.dbapi_connection
        sync_author_tokens(raw, book.id, book.authors)
        return FileScanResult("indexed", book)
    except EbookMetaError:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise


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

    book_files, scanned_roots, path_errors = collect_book_files_from_config(paths)
    stats.errors.extend(path_errors)
    total = len(book_files)
    seen_paths: set[str] = set()

    if on_progress and total:
        print(f"Scanning {total:,} ebook file(s)...", flush=True)

    for index, file_path in enumerate(book_files, start=1):
        stats.scanned += 1
        file_path = ensure_safe_filename(file_path.resolve())
        file_path_str = str(file_path)
        seen_paths.add(file_path_str)
        try:
            result = scan_single_file(
                session,
                file_path,
                ebook_meta_cmd=ebook_meta_cmd,
                covers_dir=covers_dir,
                now=now,
                verbose=verbose,
                scan_roots=scanned_roots,
            )
            if result.status == "unchanged":
                stats.unchanged += 1
                if on_progress:
                    on_progress(index, total, file_path, "unchanged")
                elif verbose:
                    print(f"unchanged: {display_name(file_path)}", flush=True)
            elif result.status == "duplicate":
                stats.skipped += 1
                if on_progress:
                    on_progress(index, total, file_path, "duplicate")
                elif verbose:
                    print(f"duplicate: {display_name(file_path)}", flush=True)
            elif result.status == "repointed":
                stats.added_or_updated += 1
                stats.files_deleted += result.files_deleted
                if on_progress:
                    on_progress(index, total, file_path, "repointed")
                elif verbose and result.book is not None:
                    title = display_text(result.book.title or result.book.file_name)
                    print(f"repointed: {title} -> {display_name(file_path)}", flush=True)
            elif result.status == "indexed":
                stats.added_or_updated += 1
                if on_progress:
                    on_progress(index, total, file_path, "indexed")
                elif verbose and result.book is not None:
                    title = display_text(result.book.title or result.book.file_name)
                    print(f"indexed: {title}", flush=True)
        except EbookMetaError as exc:
            stats.errors.append(f"{display_path(file_path)}: {exc}")
            if on_progress:
                on_progress(index, total, file_path, "error")
        except Exception as exc:
            stats.errors.append(f"{display_path(file_path)}: {exc}")
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

    raw = session.connection().connection.dbapi_connection
    refresh_library_stats(raw)

    return stats
