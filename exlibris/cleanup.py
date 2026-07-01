from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from exlibris.book_paths import collect_book_files, keeper_path, prune_empty_directories
from exlibris.file_hash import sha1_file


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


@dataclass
class AuditReport:
    unindexed_files: list[Path] = field(default_factory=list)
    duplicate_groups: list[DuplicateGroup] = field(default_factory=list)
    new_files: list[Path] = field(default_factory=list)
    absent_books: list[BookRecord] = field(default_factory=list)
    orphan_covers: list[Path] = field(default_factory=list)
    null_hash_books: list[BookRecord] = field(default_factory=list)
    out_of_root_books: list[BookRecord] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class CleanupResult:
    files_deleted: int = 0
    rows_updated: int = 0
    rows_indexed: int = 0
    rows_purged: int = 0
    covers_removed: int = 0
    hashes_backfilled: int = 0
    dirs_pruned: int = 0
    errors: list[str] = field(default_factory=list)


def is_path_under_root(path: Path, root: Path) -> bool:
    path = path.resolve()
    root = root.resolve()
    return path == root or root in path.parents


def path_is_under_any_root(path: Path, roots: list[Path]) -> bool:
    return any(is_path_under_root(path, root) for root in roots)


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
    for cover_file in covers_dir.iterdir():
        if not cover_file.is_file():
            continue
        try:
            book_id = int(cover_file.stem)
        except ValueError:
            orphans.append(cover_file)
            continue
        if book_id not in valid_book_ids:
            orphans.append(cover_file)
    return sorted(orphans)


def _group_unindexed_by_hash(
    unindexed: list[Path],
    *,
    errors: list[str],
) -> dict[str, list[Path]]:
    by_hash: dict[str, list[Path]] = defaultdict(list)
    for path in unindexed:
        try:
            by_hash[sha1_file(path)].append(path)
        except OSError as exc:
            errors.append(f"{path}: {exc}")
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

    unindexed_by_hash = _group_unindexed_by_hash(unindexed, errors=report.errors)
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
    return report


def update_book_path(
    conn: sqlite3.Connection,
    book_id: int,
    keeper: Path,
) -> None:
    """Point an existing row at a new file path without re-extracting metadata."""
    keeper = keeper.resolve()
    stat = keeper.stat()
    conn.execute(
        """
        UPDATE books
        SET file_path = ?, file_name = ?, file_size = ?, file_mtime = ?, is_missing = 0
        WHERE id = ?
        """,
        (str(keeper), keeper.name, stat.st_size, stat.st_mtime, book_id),
    )
    conn.commit()


def purge_book(conn: sqlite3.Connection, book_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM user_favorites WHERE book_id = ?",
        (book_id,),
    ).fetchone()
    favorites = int(row[0]) if row else 0
    conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
    conn.commit()
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
                path.unlink()
                files_deleted += 1
            except OSError as exc:
                raise OSError(f"failed to delete {path}: {exc}") from exc
        else:
            files_deleted += 1

    return rows_updated, files_deleted, repointed_book_id


def backfill_content_hashes(
    conn: sqlite3.Connection,
    *,
    execute: bool,
) -> tuple[int, list[str]]:
    """Compute SHA-1 for rows with NULL content_hash when the file exists."""
    errors: list[str] = []
    updated = 0
    for book in load_books(conn):
        if book.content_hash is not None:
            continue
        path = Path(book.file_path)
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
            conn.execute(
                "UPDATE books SET content_hash = ? WHERE id = ?",
                (content_hash, book.id),
            )
            conn.commit()
        updated += 1
    return updated, errors
