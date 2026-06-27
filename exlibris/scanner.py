from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from exlibris.config import PROJECT_ROOT, resolve_covers_dir, resolve_scan_path
from exlibris.database import upsert_book
from exlibris.ebook_meta import EbookMetaError, extract_cover, read_metadata

SUPPORTED_EXTENSIONS = {".epub"}


@dataclass
class ScanStats:
    scanned: int = 0
    added_or_updated: int = 0
    skipped: int = 0
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


def scan_paths(
    session: Session,
    paths: list[Path],
    *,
    ebook_meta_cmd: str | None = None,
    covers_dir: Path | None = None,
    verbose: bool = False,
) -> ScanStats:
    stats = ScanStats()
    now = datetime.now(timezone.utc)
    covers_root = resolve_covers_dir(covers_dir)

    for root in paths:
        root = resolve_scan_path(root)
        if not root.exists():
            stats.errors.append(f"path not found: {root}")
            continue

        for file_path in iter_book_files(root):
            stats.scanned += 1
            try:
                stat = file_path.stat()
                meta = read_metadata(file_path, ebook_meta_cmd=ebook_meta_cmd)
                if meta.errors and verbose:
                    stats.errors.extend(
                        f"{file_path}: {err}" for err in meta.errors
                    )

                book = upsert_book(
                    session,
                    {
                        "file_path": str(file_path),
                        "file_name": file_path.name,
                        "format": meta.format or file_path.suffix.lower().lstrip("."),
                        "file_size": stat.st_size,
                        "file_mtime": stat.st_mtime,
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
                if verbose:
                    title = book.title or book.file_name
                    print(f"indexed: {title}", flush=True)
            except EbookMetaError as exc:
                session.rollback()
                stats.errors.append(f"{file_path}: {exc}")
            except Exception as exc:
                session.rollback()
                stats.errors.append(f"{file_path}: {exc}")

    return stats
