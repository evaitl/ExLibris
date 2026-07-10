from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

# current, total, label (usually a basename or path fragment)
ProgressCallback = Callable[[int, int, str], None]
InvalidEpubCallback = Callable[["InvalidEpub"], None]
# valid_count so far, total paths being checked
ValidEpubProgressCallback = Callable[[int, int], None]

VALID_EPUB_PROGRESS_INTERVAL = 1000

from exlibris.book_paths import (
    collect_book_files,
    delete_file_under_roots,
    keeper_path,
    path_is_under_any_root,
    prune_empty_directories,
)
from exlibris.cover_paths import (
    iter_cover_files,
    parse_book_id_from_cover,
    remove_cover_files,
)
from exlibris.library_cache import refresh_library_stats
from exlibris.epub_validate import validate_epub
from exlibris.ebook_meta import EbookMetaError
from exlibris.filenames import (
    display_path,
    filename_needs_update,
    target_filename,
    unique_target_path,
)
from exlibris.file_hash import sha1_file
from exlibris.description_text import description_needs_plaintext, plain_text_description
from exlibris.sqlite_retry import is_sqlite_locked, run_write_with_retry


@dataclass(frozen=True)
class BookRecord:
    id: int
    file_path: str
    file_name: str
    content_hash: str | None
    is_missing: bool


@dataclass
class DuplicateGroup:
    content_hash: str
    keeper: Path
    remove: list[Path]
    book_id: int | None = None
    repoint_only: bool = False


@dataclass(frozen=True)
class InvalidEpub:
    path: Path
    book_id: int | None
    detail: str

    def display_line(self) -> str:
        prefix = f"id={self.book_id}  " if self.book_id is not None else ""
        return f"{prefix}{display_path(self.path)}"


@dataclass(frozen=True)
class EpubRemovalContext:
    scan_roots: list[Path]
    covers_dir: Path
    execute: bool


@dataclass
class EpubRemovalTotals:
    files_deleted: int = 0
    rows_purged: int = 0
    covers_removed: int = 0


@dataclass
class AuditReport:
    unindexed_files: list[Path] = field(default_factory=list)
    duplicate_groups: list[DuplicateGroup] = field(default_factory=list)
    new_files: list[Path] = field(default_factory=list)
    absent_books: list[BookRecord] = field(default_factory=list)
    orphan_covers: list[Path] = field(default_factory=list)
    null_hash_books: list[BookRecord] = field(default_factory=list)
    out_of_root_books: list[BookRecord] = field(default_factory=list)
    filename_fixes: list[str] = field(default_factory=list)
    invalid_epubs: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class CleanupResult:
    files_deleted: int = 0
    rows_updated: int = 0
    rows_indexed: int = 0
    rows_purged: int = 0
    covers_removed: int = 0
    hashes_backfilled: int = 0
    filenames_sanitized: int = 0
    invalid_epubs: int = 0
    dirs_pruned: int = 0
    descriptions_stripped: int = 0
    errors: list[str] = field(default_factory=list)


def load_books(conn: sqlite3.Connection) -> list[BookRecord]:
    rows = conn.execute(
        """
        SELECT id, file_path, file_name, content_hash, is_missing
        FROM books
        ORDER BY id
        """
    ).fetchall()
    return [
        BookRecord(
            id=int(row["id"]),
            file_path=str(row["file_path"]),
            file_name=str(row["file_name"]),
            content_hash=row["content_hash"],
            is_missing=bool(row["is_missing"]),
        )
        for row in rows
    ]


def find_orphan_covers(covers_dir: Path, valid_book_ids: set[int]) -> list[Path]:
    if not covers_dir.is_dir():
        return []
    orphans: list[Path] = []
    for cover_file in iter_cover_files(covers_dir):
        book_id = parse_book_id_from_cover(cover_file)
        if book_id is None:
            orphans.append(cover_file)
            continue
        if book_id not in valid_book_ids:
            orphans.append(cover_file)
    return sorted(orphans)


def _group_unindexed_by_hash(
    unindexed: list[Path],
    *,
    errors: list[str],
    on_progress: ProgressCallback | None = None,
) -> dict[str, list[Path]]:
    by_hash: dict[str, list[Path]] = defaultdict(list)
    total = len(unindexed)
    for index, path in enumerate(unindexed, start=1):
        try:
            by_hash[sha1_file(path)].append(path)
        except OSError as exc:
            errors.append(f"{path}: {exc}")
        if on_progress is not None:
            on_progress(index, total, path.name)
    return by_hash


def build_duplicate_groups(
    unindexed_by_hash: dict[str, list[Path]],
    hash_to_book: dict[str, BookRecord],
) -> list[DuplicateGroup]:
    groups: list[DuplicateGroup] = []
    for content_hash, paths in sorted(unindexed_by_hash.items()):
        book = hash_to_book.get(content_hash)
        candidates = list(paths)
        if book is not None:
            db_path = Path(book.file_path)
            if db_path.is_file() and db_path not in candidates:
                candidates.append(db_path.resolve())
        keeper = keeper_path(candidates)
        remove = [path for path in candidates if path != keeper]
        if not remove and book is None and len(paths) <= 1:
            continue
        if not remove and book is not None and Path(book.file_path).resolve() == keeper.resolve():
            continue
        repoint_only = (
            book is not None
            and not remove
            and not Path(book.file_path).is_file()
        )
        groups.append(
            DuplicateGroup(
                content_hash=content_hash,
                keeper=keeper,
                remove=remove,
                book_id=book.id if book else None,
                repoint_only=repoint_only,
            )
        )
    return groups


def audit_library(
    conn: sqlite3.Connection,
    scan_roots: list[Path],
    *,
    covers_dir: Path,
    on_progress: ProgressCallback | None = None,
) -> AuditReport:
    report = AuditReport()
    books = load_books(conn)
    indexed_paths = {str(Path(book.file_path).resolve()) for book in books}
    hash_to_book = {
        book.content_hash: book
        for book in books
        if book.content_hash
    }

    disk_files, _, path_errors = collect_book_files(
        scan_roots,
        resolve_path=lambda path: path.resolve(),
    )
    report.errors.extend(path_errors)

    unindexed = [path for path in disk_files if str(path) not in indexed_paths]
    report.unindexed_files = unindexed

    unindexed_by_hash = _group_unindexed_by_hash(
        unindexed,
        errors=report.errors,
        on_progress=on_progress,
    )
    report.duplicate_groups = build_duplicate_groups(unindexed_by_hash, hash_to_book)

    report.new_files = [
        keeper_path(paths)
        for content_hash, paths in sorted(unindexed_by_hash.items())
        if content_hash not in hash_to_book
    ]

    for book in books:
        path = Path(book.file_path)
        if not path.is_file():
            report.absent_books.append(book)
        if book.content_hash is None:
            report.null_hash_books.append(book)
        if not path_is_under_any_root(path, scan_roots):
            report.out_of_root_books.append(book)

    valid_ids = {book.id for book in books}
    report.orphan_covers = find_orphan_covers(covers_dir, valid_ids)
    report.filename_fixes = list_filename_fixes(conn, scan_roots)
    return report


def update_book_path(
    conn: sqlite3.Connection,
    book_id: int,
    keeper: Path,
) -> None:
    """Point an existing row at a new file path without re-extracting metadata."""
    keeper = keeper.resolve()
    stat = keeper.stat()
    params = (str(keeper), keeper.name, stat.st_size, stat.st_mtime, book_id)

    def writer() -> None:
        try:
            conn.execute(
                """
                UPDATE books
                SET file_path = ?, file_name = ?, file_size = ?, file_mtime = ?,
                    is_missing = 0, epub_validated = 0, epub_deep_validated = 0,
                    epub_version2 = 0
                WHERE id = ?
                """,
                params,
            )
        except sqlite3.OperationalError as exc:
            if is_sqlite_locked(exc):
                raise
            try:
                conn.execute(
                    """
                    UPDATE books
                    SET file_path = ?, file_name = ?, file_size = ?, file_mtime = ?,
                        is_missing = 0, epub_validated = 0, epub_deep_validated = 0
                    WHERE id = ?
                    """,
                    params,
                )
            except sqlite3.OperationalError as exc2:
                if is_sqlite_locked(exc2):
                    raise
                conn.execute(
                    """
                    UPDATE books
                    SET file_path = ?, file_name = ?, file_size = ?, file_mtime = ?,
                        is_missing = 0
                    WHERE id = ?
                    """,
                    params,
                )

    run_write_with_retry(conn, writer)


def purge_book(conn: sqlite3.Connection, book_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM user_favorites WHERE book_id = ?",
        (book_id,),
    ).fetchone()
    favorites = int(row[0]) if row else 0

    def writer() -> None:
        conn.execute("DELETE FROM books WHERE id = ?", (book_id,))

    run_write_with_retry(conn, writer)
    refresh_library_stats(conn)
    return favorites


def apply_duplicate_group(
    conn: sqlite3.Connection,
    group: DuplicateGroup,
    *,
    scan_roots: list[Path],
    execute: bool,
) -> tuple[int, int, int | None]:
    """Update DB repoint and delete duplicate files.

    Returns (rows_updated, files_deleted, repointed_book_id).
    Repoint-only moves update path fields only; bibliographic metadata is unchanged.
    """
    rows_updated = 0
    files_deleted = 0
    repointed_book_id: int | None = None

    if group.book_id is not None:
        current = conn.execute(
            "SELECT file_path FROM books WHERE id = ?",
            (group.book_id,),
        ).fetchone()
        if current is not None and Path(str(current["file_path"])).resolve() != group.keeper.resolve():
            if execute:
                update_book_path(conn, group.book_id, group.keeper)
            rows_updated = 1
            repointed_book_id = group.book_id

    for path in group.remove:
        if not path_is_under_any_root(path, scan_roots):
            raise OSError(f"refusing to delete outside scan roots: {path}")
        if execute:
            try:
                delete_file_under_roots(path, scan_roots)
                files_deleted += 1
            except OSError as exc:
                raise OSError(f"failed to delete {path}: {exc}") from exc
        else:
            files_deleted += 1

    return rows_updated, files_deleted, repointed_book_id


def list_filename_fixes(
    conn: sqlite3.Connection,
    scan_roots: list[Path],
    *,
    max_short_stem_len: int = 10,
) -> list[str]:
    fixes: list[str] = []
    for row in _filename_fixup_rows(conn):
        path = Path(row.file_path)
        if not path.is_file():
            continue
        resolved = path.resolve()
        if not path_is_under_any_root(resolved, scan_roots):
            continue
        desired = target_filename(
            path.name,
            title=row.title,
            authors=row.authors,
            publisher=row.publisher,
            max_short_stem_len=max_short_stem_len,
        )
        if (
            desired == path.name
            and str(resolved) == row.file_path
            and row.file_name == desired
        ):
            continue
        fixes.append(
            f"id={row.id}  {display_path(resolved)} -> {desired}"
        )
    return fixes


@dataclass(frozen=True)
class _FilenameRow:
    id: int
    file_path: str
    file_name: str
    title: str | None
    authors: str | None
    publisher: str | None


def _filename_fixup_rows(conn: sqlite3.Connection) -> list[_FilenameRow]:
    try:
        rows = conn.execute(
            """
            SELECT id, file_path, file_name, title, authors, publisher
            FROM books
            WHERE is_missing = 0
            ORDER BY id
            """
        ).fetchall()
        return [
            _FilenameRow(
                id=int(row["id"]),
                file_path=str(row["file_path"]),
                file_name=str(row["file_name"]),
                title=row["title"],
                authors=row["authors"],
                publisher=row["publisher"],
            )
            for row in rows
        ]
    except sqlite3.OperationalError:
        return [
            _FilenameRow(
                id=book.id,
                file_path=book.file_path,
                file_name=book.file_name,
                title=None,
                authors=None,
                publisher=None,
            )
            for book in load_books(conn)
            if not book.is_missing
        ]


def sanitize_book_filenames(
    conn: sqlite3.Connection,
    scan_roots: list[Path],
    *,
    execute: bool,
    max_short_stem_len: int = 10,
    on_progress: ProgressCallback | None = None,
) -> tuple[int, list[str]]:
    """Rename unsafe or very short basenames and update indexed rows."""
    errors: list[str] = []
    updated = 0
    rows = _filename_fixup_rows(conn)
    total = len(rows)

    for index, row in enumerate(rows, start=1):
        path = Path(row.file_path)
        if on_progress is not None:
            on_progress(index, total, path.name)
        if not path.is_file():
            continue
        resolved = path.resolve()
        if not path_is_under_any_root(resolved, scan_roots):
            continue

        desired_name = target_filename(
            path.name,
            title=row.title,
            authors=row.authors,
            publisher=row.publisher,
            max_short_stem_len=max_short_stem_len,
        )
        target = resolved
        if desired_name != path.name:
            candidate = unique_target_path(resolved.parent, desired_name)
            if candidate.exists() and candidate.resolve() != resolved:
                errors.append(
                    f"id={row.id}: cannot rename to {display_path(candidate)}, exists"
                )
                continue
            target = candidate.resolve()
            if execute:
                try:
                    resolved.rename(target)
                except OSError as exc:
                    errors.append(f"id={row.id}: {exc}")
                    continue

        needs_db_update = (
            str(target) != row.file_path
            or target.name != row.file_name
            or filename_needs_update(
                row.file_name,
                title=row.title,
                authors=row.authors,
                publisher=row.publisher,
                max_short_stem_len=max_short_stem_len,
            )
        )
        if not needs_db_update:
            continue
        if execute:
            update_book_path(conn, row.id, target)
        updated += 1

    return updated, errors


def _books_have_epub_validation_columns(conn: sqlite3.Connection) -> bool:
    rows = conn.execute("PRAGMA table_info(books)").fetchall()
    names = {str(row[1]) for row in rows}
    return "epub_validated" in names and "epub_deep_validated" in names


def mark_epub_validated_batch(
    conn: sqlite3.Connection,
    book_ids: list[int],
    *,
    deep: bool,
) -> None:
    """Record successful EPUB validation for indexed books."""
    if not book_ids:
        return
    try:
        placeholders = ",".join("?" * len(book_ids))

        def writer() -> None:
            if deep:
                conn.execute(
                    f"""
                    UPDATE books
                    SET epub_validated = 1, epub_deep_validated = 1
                    WHERE id IN ({placeholders})
                    """,
                    book_ids,
                )
            else:
                conn.execute(
                    f"""
                    UPDATE books SET epub_validated = 1
                    WHERE id IN ({placeholders})
                    """,
                    book_ids,
                )

        run_write_with_retry(conn, writer)
    except sqlite3.OperationalError:
        return


def mark_epub_validated(
    conn: sqlite3.Connection,
    book_id: int,
    *,
    deep: bool,
) -> None:
    """Record one indexed book as successfully validated."""
    mark_epub_validated_batch(conn, [book_id], deep=deep)


def audit_epub_integrity(
    paths: list[Path],
    *,
    path_to_book_id: dict[str, int] | None = None,
    deep: bool = False,
    ebook_meta_cmd: str | None = None,
    conn: sqlite3.Connection | None = None,
    removal: EpubRemovalContext | None = None,
    removal_totals: EpubRemovalTotals | None = None,
    on_progress: ProgressCallback | None = None,
    on_invalid: InvalidEpubCallback | None = None,
    on_valid_progress: ValidEpubProgressCallback | None = None,
    valid_progress_interval: int = VALID_EPUB_PROGRESS_INTERVAL,
) -> tuple[list[InvalidEpub], list[str]]:
    """Validate EPUB files on disk. Returns (invalid items, errors).

    ``on_invalid`` is called as soon as each invalid EPUB is found.
    ``on_valid_progress`` is called after every ``valid_progress_interval``
    valid EPUBs (default 1000). When ``conn`` is provided, indexed books that
    pass validation are marked immediately so later runs can skip them.
    When ``removal`` is provided, each invalid EPUB is handled as soon as it
    is found instead of waiting until the end of the pass.
    """
    path_to_book_id = path_to_book_id or {}
    invalid: list[InvalidEpub] = []
    errors: list[str] = []
    ordered = sorted(paths, key=lambda item: str(item).lower())
    total = len(ordered)
    valid_count = 0
    for index, path in enumerate(ordered, start=1):
        if on_progress is not None:
            on_progress(index, total, path.name)
        try:
            result = validate_epub(
                path,
                deep=deep,
                ebook_meta_cmd=ebook_meta_cmd,
            )
        except EbookMetaError as exc:
            errors.append(f"{path}: {exc}")
            continue
        if not result.ok:
            detail = "; ".join(result.errors) if result.errors else "invalid"
            item = InvalidEpub(
                path=path.resolve(),
                book_id=path_to_book_id.get(str(path.resolve())),
                detail=detail,
            )
            invalid.append(item)
            if on_invalid is not None:
                on_invalid(item)
            if removal is not None and conn is not None:
                files_deleted, rows_purged, covers_removed, remove_errors = (
                    remove_invalid_epubs(
                        conn,
                        [item],
                        scan_roots=removal.scan_roots,
                        covers_dir=removal.covers_dir,
                        execute=removal.execute,
                    )
                )
                if removal_totals is not None:
                    removal_totals.files_deleted += files_deleted
                    removal_totals.rows_purged += rows_purged
                    removal_totals.covers_removed += covers_removed
                errors.extend(remove_errors)
            continue
        valid_count += 1
        book_id = path_to_book_id.get(str(path.resolve()))
        if conn is not None and book_id is not None:
            mark_epub_validated(conn, book_id, deep=deep)
        if (
            on_valid_progress is not None
            and valid_progress_interval > 0
            and valid_count % valid_progress_interval == 0
        ):
            on_valid_progress(valid_count, total)
    return invalid, errors


def build_path_to_book_id(conn: sqlite3.Connection) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for row in conn.execute("SELECT id, file_path FROM books"):
        try:
            mapping[str(Path(str(row["file_path"])).resolve())] = int(row["id"])
        except (OSError, ValueError, TypeError):
            continue
    return mapping


def remove_invalid_epubs(
    conn: sqlite3.Connection,
    invalid: list[InvalidEpub],
    *,
    scan_roots: list[Path],
    covers_dir: Path,
    execute: bool,
) -> tuple[int, int, int, list[str]]:
    """Delete invalid EPUB files and purge matching database rows.

    Returns (files_deleted, rows_purged, covers_removed, errors).
    """
    files_deleted = 0
    rows_purged = 0
    covers_removed = 0
    errors: list[str] = []
    seen_paths: set[str] = set()

    for item in invalid:
        path = item.path.resolve()
        key = str(path)
        if key in seen_paths:
            continue
        seen_paths.add(key)

        if path.is_file():
            if not path_is_under_any_root(path, scan_roots):
                errors.append(f"refusing to delete outside scan roots: {path}")
                continue
            if execute:
                try:
                    delete_file_under_roots(path, scan_roots)
                    files_deleted += 1
                except OSError as exc:
                    errors.append(f"{path}: {exc}")
                    continue
            else:
                files_deleted += 1

        if item.book_id is None:
            continue

        if execute:
            if covers_dir.is_dir():
                remove_cover_files(covers_dir, item.book_id)
                covers_removed += 1
            purge_book(conn, item.book_id)
            rows_purged += 1
        else:
            rows_purged += 1
            if covers_dir.is_dir():
                covers_removed += 1

    return files_deleted, rows_purged, covers_removed, errors


def collect_epub_paths_for_validation(
    conn: sqlite3.Connection,
    scan_roots: list[Path],
    *,
    deep: bool = False,
) -> tuple[list[Path], int]:
    """Indexed on-disk books plus unindexed EPUBs under scan roots.

    Returns (paths to validate, skipped indexed books already validated).
    """
    paths: list[Path] = []
    seen: set[str] = set()
    skipped = 0
    has_validation_columns = _books_have_epub_validation_columns(conn)
    validation_filter = ""
    if has_validation_columns:
        validation_filter = (
            " AND epub_deep_validated = 0" if deep else " AND epub_validated = 0"
        )
        skipped = int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                FROM books
                WHERE is_missing = 0
                  AND {"epub_deep_validated = 1" if deep else "epub_validated = 1"}
                """
            ).fetchone()[0]
        )
        skip_rows = conn.execute(
            f"""
            SELECT file_path
            FROM books
            WHERE is_missing = 0
              AND {"epub_deep_validated = 1" if deep else "epub_validated = 1"}
            ORDER BY id
            """
        ).fetchall()
        for row in skip_rows:
            path = Path(str(row["file_path"]))
            if not path.is_file():
                continue
            resolved = path.resolve()
            if path_is_under_any_root(resolved, scan_roots):
                seen.add(str(resolved))
    rows = conn.execute(
        f"""
        SELECT id, file_path
        FROM books
        WHERE is_missing = 0
        {validation_filter}
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
        key = str(resolved)
        if key not in seen:
            seen.add(key)
            paths.append(resolved)

    disk_files, _, _path_errors = collect_book_files(
        scan_roots,
        resolve_path=lambda item: item.resolve(),
    )
    for path in disk_files:
        key = str(path)
        if key not in seen:
            seen.add(key)
            paths.append(path)
    return paths, skipped


def backfill_content_hashes(
    conn: sqlite3.Connection,
    *,
    execute: bool,
    on_progress: ProgressCallback | None = None,
) -> tuple[int, list[str]]:
    """Compute SHA-1 for rows with NULL content_hash when the file exists."""
    errors: list[str] = []
    updated = 0
    candidates = [
        book for book in load_books(conn) if book.content_hash is None
    ]
    total = len(candidates)
    for index, book in enumerate(candidates, start=1):
        path = Path(book.file_path)
        if on_progress is not None:
            on_progress(index, total, path.name)
        if not path.is_file():
            continue
        try:
            content_hash = sha1_file(path)
        except OSError as exc:
            errors.append(f"{path}: {exc}")
            continue
        conflict = conn.execute(
            "SELECT id FROM books WHERE content_hash = ? AND id != ?",
            (content_hash, book.id),
        ).fetchone()
        if conflict is not None:
            errors.append(
                f"id={book.id}: same hash as existing row id={int(conflict['id'])}"
            )
            continue
        if execute:
            def writer() -> None:
                try:
                    conn.execute(
                        "UPDATE books SET content_hash = ?, epub_validated = 0, "
                        "epub_deep_validated = 0, epub_version2 = 0 WHERE id = ?",
                        (content_hash, book.id),
                    )
                except sqlite3.OperationalError as exc:
                    if is_sqlite_locked(exc):
                        raise
                    conn.execute(
                        "UPDATE books SET content_hash = ? WHERE id = ?",
                        (content_hash, book.id),
                    )

            run_write_with_retry(conn, writer)
        updated += 1
    return updated, errors


def strip_book_descriptions(
    conn: sqlite3.Connection,
    *,
    execute: bool,
    on_progress: ProgressCallback | None = None,
) -> tuple[int, list[str]]:
    """Replace HTML descriptions with plain text (tags removed, entities decoded)."""
    errors: list[str] = []
    updated = 0
    rows = conn.execute(
        """
        SELECT id, file_name, description
        FROM books
        WHERE description IS NOT NULL AND description != ''
        ORDER BY id
        """
    ).fetchall()
    total = len(rows)
    for index, row in enumerate(rows, start=1):
        book_id = int(row["id"])
        label = str(row["file_name"])
        original = str(row["description"])
        if on_progress is not None:
            on_progress(index, total, label)
        if not description_needs_plaintext(original):
            continue
        cleaned = plain_text_description(original)
        if execute:
            run_write_with_retry(
                conn,
                lambda cleaned=cleaned, book_id=book_id: conn.execute(
                    "UPDATE books SET description = ? WHERE id = ?",
                    (cleaned, book_id),
                ),
            )
        updated += 1
    return updated, errors
