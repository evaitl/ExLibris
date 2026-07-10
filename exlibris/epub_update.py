"""Re-encode indexed EPUBs to EPUB 2 via Calibre ebook-convert."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from exlibris.book_paths import delete_file_under_roots, path_is_under_any_root
from exlibris.cleanup import purge_book
from exlibris.cover_paths import remove_cover_files
from exlibris.ebook_convert import EbookConvertError, convert_epub_to_version2
from exlibris.epub_validate import validate_epub_structure
from exlibris.file_hash import sha1_file
from exlibris.sqlite_retry import run_write_with_retry

UpdateStartCallback = Callable[[int, int], None]
UpdateEventCallback = Callable[[int, int, str, str, str], None]

SUCCESS_LOG_INTERVAL = 100


@dataclass(frozen=True)
class EpubUpdateRow:
    id: int
    file_path: str
    file_name: str


@dataclass
class EpubUpdateStats:
    candidates: int = 0
    converted: int = 0
    removed: int = 0
    skipped_converted: int = 0
    skipped_missing: int = 0
    errors: list[str] = field(default_factory=list)


def _has_epub_version2_column(conn: sqlite3.Connection) -> bool:
    rows = conn.execute("PRAGMA table_info(books)").fetchall()
    return any(str(row[1]) == "epub_version2" for row in rows)


def list_epubs_needing_update(
    conn: sqlite3.Connection,
    scan_roots: list[Path],
) -> tuple[list[EpubUpdateRow], int]:
    """Return on-disk EPUB rows under scan roots that are not yet converted."""
    if not _has_epub_version2_column(conn):
        raise sqlite3.OperationalError("books.epub_version2 column is missing")

    skipped_converted = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM books
            WHERE is_missing = 0
              AND LOWER(format) = 'epub'
              AND epub_version2 = 1
            """
        ).fetchone()[0]
    )
    candidates: list[EpubUpdateRow] = []
    rows = conn.execute(
        """
        SELECT id, file_path, file_name
        FROM books
        WHERE is_missing = 0
          AND LOWER(format) = 'epub'
          AND epub_version2 = 0
        ORDER BY id
        """
    ).fetchall()
    for row in rows:
        path = Path(str(row["file_path"]))
        if not path.is_file():
            continue
        resolved = path.resolve()
        if not path_is_under_any_root(resolved, scan_roots):
            continue
        candidates.append(
            EpubUpdateRow(
                id=int(row["id"]),
                file_path=str(resolved),
                file_name=str(row["file_name"]),
            )
        )
    return candidates, skipped_converted


def _mark_converted(
    conn: sqlite3.Connection,
    *,
    book_id: int,
    content_hash: str,
    file_size: int,
    file_mtime: float,
    epub_validated: bool,
) -> None:
    def writer() -> None:
        conn.execute(
            """
            UPDATE books
            SET content_hash = ?,
                file_size = ?,
                file_mtime = ?,
                epub_version2 = 1,
                epub_validated = ?,
                epub_deep_validated = 0
            WHERE id = ?
            """,
            (
                content_hash,
                file_size,
                file_mtime,
                1 if epub_validated else 0,
                book_id,
            ),
        )

    run_write_with_retry(conn, writer)


def _remove_failed_book(
    conn: sqlite3.Connection,
    *,
    book_id: int,
    file_path: Path,
    scan_roots: list[Path],
    covers_dir: Path,
    execute: bool,
) -> None:
    if execute and file_path.is_file():
        if path_is_under_any_root(file_path, scan_roots):
            delete_file_under_roots(file_path, scan_roots)
    if execute:
        if covers_dir.is_dir():
            remove_cover_files(covers_dir, book_id)
        purge_book(conn, book_id)


def update_epubs(
    conn: sqlite3.Connection,
    scan_roots: list[Path],
    covers_dir: Path,
    *,
    execute: bool,
    ebook_convert_cmd: str | None = None,
    on_start: UpdateStartCallback | None = None,
    on_event: UpdateEventCallback | None = None,
) -> EpubUpdateStats:
    stats = EpubUpdateStats()
    candidates, stats.skipped_converted = list_epubs_needing_update(conn, scan_roots)
    stats.candidates = len(candidates)
    total = len(candidates)
    if on_start is not None:
        on_start(total, stats.skipped_converted)

    for index, row in enumerate(candidates, start=1):
        path = Path(row.file_path)

        if not path.is_file():
            stats.skipped_missing += 1
            if on_event is not None:
                on_event(index, total, path.name, "missing", "")
            continue

        if not execute:
            stats.converted += 1
            if on_event is not None:
                on_event(index, total, path.name, "would_convert", "")
            continue

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
            validation = validate_epub_structure(path)
            if not validation.ok:
                detail = "; ".join(validation.errors) or "EPUB validation failed"
                stats.errors.append(f"id={row.id} {path.name}: {detail}")
                _remove_failed_book(
                    conn,
                    book_id=row.id,
                    file_path=path,
                    scan_roots=scan_roots,
                    covers_dir=covers_dir,
                    execute=execute,
                )
                stats.removed += 1
                if on_event is not None:
                    on_event(index, total, path.name, "removed", detail)
                continue
            stat = path.stat()
            content_hash = sha1_file(path)
            _mark_converted(
                conn,
                book_id=row.id,
                content_hash=content_hash,
                file_size=stat.st_size,
                file_mtime=stat.st_mtime,
                epub_validated=True,
            )
            stats.converted += 1
            if on_event is not None:
                on_event(index, total, path.name, "converted", "")
        except (EbookConvertError, OSError) as exc:
            if temp_path is not None and temp_path.is_file():
                try:
                    temp_path.unlink()
                except OSError:
                    pass
            detail = str(exc)
            stats.errors.append(f"id={row.id} {path.name}: {detail}")
            _remove_failed_book(
                conn,
                book_id=row.id,
                file_path=path,
                scan_roots=scan_roots,
                covers_dir=covers_dir,
                execute=execute,
            )
            stats.removed += 1
            if on_event is not None:
                on_event(index, total, path.name, "removed", detail)

    return stats
