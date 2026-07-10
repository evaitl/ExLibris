#!/usr/bin/env python3
"""Move flat cover images into shard subdirs and update books.cover_path."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_VENV_PYTHON = _ROOT / ".venv" / "bin" / "python"


def _ensure_project_python() -> None:
    if os.environ.get("EXLIBRIS_REEXEC") == "1":
        return
    try:
        import sqlalchemy  # noqa: F401
    except ModuleNotFoundError:
        if _VENV_PYTHON.is_file():
            os.environ["EXLIBRIS_REEXEC"] = "1"
            os.execv(str(_VENV_PYTHON), [str(_VENV_PYTHON), *sys.argv])
        print(
            "error: run from the project venv:\n"
            "  .venv/bin/python scripts/shard-covers.py",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


_ensure_project_python()

import argparse
import shutil
import sqlite3
from dataclasses import dataclass, field

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from exlibris.config import PROJECT_ROOT, resolve_covers_dir, resolve_database_path
from exlibris.cover_paths import (
    cover_public_segment,
    cover_relative_path,
    cover_shard,
    cover_storage_path,
    is_flat_cover,
    iter_cover_files,
    parse_book_id_from_cover,
)


@dataclass
class ShardStats:
    moved: int = 0
    db_updated: int = 0
    already_sharded: int = 0
    orphans_moved: int = 0
    errors: list[str] = field(default_factory=list)


def _resolve_database(args: argparse.Namespace) -> Path:
    if args.database:
        path = Path(args.database).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path.resolve()
    return resolve_database_path()


def _resolve_covers(args: argparse.Namespace) -> Path:
    if args.covers_dir:
        path = Path(args.covers_dir).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path.resolve()
    return resolve_covers_dir()


def _expected_relative_path(
    covers_root: Path,
    book_id: int,
    suffix: str,
) -> str:
    return cover_relative_path(
        covers_root,
        book_id,
        suffix,
        project_root=PROJECT_ROOT,
    )


def _move_to_shard(
    covers_root: Path,
    book_id: int,
    source: Path,
    *,
    execute: bool,
) -> Path | None:
    dest = cover_storage_path(covers_root, book_id, source.suffix)
    if source.resolve() == dest.resolve():
        return dest
    if not execute:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.resolve() != source.resolve():
        raise OSError(f"destination already exists: {dest}")
    shutil.move(str(source), str(dest))
    return dest


def shard_covers(
    conn: sqlite3.Connection,
    covers_root: Path,
    *,
    execute: bool,
) -> ShardStats:
    stats = ShardStats()
    valid_ids = {
        int(row[0])
        for row in conn.execute("SELECT id FROM books").fetchall()
    }

    by_book: dict[int, Path] = {}
    for path in iter_cover_files(covers_root):
        book_id = parse_book_id_from_cover(path)
        if book_id is None:
            stats.errors.append(f"unrecognized cover filename: {path}")
            continue
        existing = by_book.get(book_id)
        if existing is None or is_flat_cover(path, covers_root):
            by_book[book_id] = path

    for book_id in sorted(by_book):
        source = by_book[book_id]
        dest = cover_storage_path(covers_root, book_id, source.suffix)
        rel = _expected_relative_path(covers_root, book_id, source.suffix)

        if source.resolve() == dest.resolve():
            stats.already_sharded += 1
        else:
            try:
                _move_to_shard(covers_root, book_id, source, execute=execute)
                stats.moved += 1
                if book_id not in valid_ids:
                    stats.orphans_moved += 1
            except OSError as exc:
                stats.errors.append(f"book {book_id}: {exc}")
                continue

        if book_id in valid_ids:
            row = conn.execute(
                "SELECT cover_path FROM books WHERE id = ?",
                (book_id,),
            ).fetchone()
            if row is not None and row["cover_path"] != rel:
                if execute:
                    conn.execute(
                        "UPDATE books SET cover_path = ? WHERE id = ?",
                        (rel, book_id),
                    )
                stats.db_updated += 1

    for row in conn.execute(
        "SELECT id, cover_path FROM books WHERE cover_path IS NOT NULL"
    ):
        book_id = int(row["id"])
        cover_path = str(row["cover_path"])
        segment = cover_public_segment(cover_path)
        suffix = Path(segment).suffix or ".jpg"
        rel = _expected_relative_path(covers_root, book_id, suffix)
        if cover_path == rel and segment.startswith(f"{cover_shard(book_id)}/"):
            continue

        on_disk = cover_storage_path(covers_root, book_id, suffix)
        if not on_disk.is_file():
            flat = covers_root / f"{book_id}{suffix}"
            if flat.is_file():
                try:
                    _move_to_shard(covers_root, book_id, flat, execute=execute)
                    stats.moved += 1
                except OSError as exc:
                    stats.errors.append(f"book {book_id}: {exc}")
                    continue
            else:
                continue

        if cover_path != rel:
            if execute:
                conn.execute(
                    "UPDATE books SET cover_path = ? WHERE id = ?",
                    (rel, book_id),
                )
            stats.db_updated += 1

    if execute:
        conn.commit()

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Shard cover images into data/covers/NN/ subdirectories."
    )
    parser.add_argument(
        "--database",
        "-d",
        type=Path,
        help="Path to library.db (default: from config or data/library.db)",
    )
    parser.add_argument(
        "--covers-dir",
        "-c",
        type=Path,
        help="Covers directory (default: from config or data/covers)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply moves and database updates (dry-run by default)",
    )
    args = parser.parse_args()

    db_path = _resolve_database(args)
    covers_root = _resolve_covers(args)

    if not db_path.is_file():
        print(f"Database not found: {db_path}", file=sys.stderr)
        raise SystemExit(1)
    if not covers_root.is_dir():
        print(f"Covers directory not found: {covers_root}", file=sys.stderr)
        raise SystemExit(1)

    mode = "EXECUTE" if args.execute else "DRY RUN"
    print(f"{mode}: sharding covers in {covers_root}")
    print(f"Database: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        stats = shard_covers(conn, covers_root, execute=args.execute)
    finally:
        conn.close()

    print(
        f"{'Moved' if args.execute else 'Would move'} {stats.moved} file(s), "
        f"{'updated' if args.execute else 'would update'} {stats.db_updated} row(s), "
        f"{stats.already_sharded} already sharded"
    )
    if stats.orphans_moved:
        print(f"Orphan covers (no DB row): {stats.orphans_moved}")
    if stats.errors:
        print(f"{len(stats.errors)} error(s):", file=sys.stderr)
        for err in stats.errors[:20]:
            print(f"  - {err}", file=sys.stderr)
        if len(stats.errors) > 20:
            print(f"  ... and {len(stats.errors) - 20} more", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
