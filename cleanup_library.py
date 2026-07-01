#!/usr/bin/env python3
"""Audit and clean up ExLibris library files vs the database."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

from exlibris.cleanup import (
    AuditReport,
    CleanupResult,
    apply_duplicate_group,
    audit_library,
    backfill_content_hashes,
    find_orphan_covers,
    load_books,
    purge_book,
)
from exlibris.book_paths import prune_empty_directories

PROJECT_ROOT = Path(__file__).resolve().parent


def _resolve_project_path(path: Path) -> Path:
    path = path.expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _load_yaml_config(config: Path | None) -> dict:
    path = config.expanduser() if config else PROJECT_ROOT / "config.yaml"
    if not path.is_file():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def _database_path(args: argparse.Namespace) -> Path:
    if args.database is not None:
        return _resolve_project_path(args.database)
    env = os.environ.get("EXLIBRIS_DATABASE_PATH")
    if env:
        return Path(env).expanduser().resolve()
    data = _load_yaml_config(args.config)
    if data.get("database_path"):
        return _resolve_project_path(Path(data["database_path"]))
    return _resolve_project_path(Path("data/library.db"))


def _covers_dir(args: argparse.Namespace) -> Path:
    data = _load_yaml_config(args.config)
    if data.get("covers_dir"):
        return _resolve_project_path(Path(data["covers_dir"]))
    return _resolve_project_path(Path("data/covers"))


def _scan_roots(args: argparse.Namespace) -> list[Path]:
    if args.path:
        return [_resolve_project_path(path) for path in args.path]
    data = _load_yaml_config(args.config)
    raw_paths = data.get("scan_paths")
    if isinstance(raw_paths, list) and raw_paths:
        return [_resolve_project_path(Path(str(path))) for path in raw_paths]
    return [Path("/media/books").resolve()]


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--database",
        "-d",
        type=Path,
        default=None,
        help="SQLite database path (default: data/library.db or config.yaml)",
    )
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        default=None,
        help="Path to config.yaml",
    )
    parser.add_argument(
        "--path",
        "-p",
        type=Path,
        action="append",
        default=None,
        help="Scan root (repeatable; overrides config scan_paths)",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress per-item output",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose output",
    )


def _print_section(title: str, lines: list[str], *, quiet: bool) -> None:
    if quiet and not lines:
        return
    print(f"\n== {title} ({len(lines)}) ==")
    if not lines:
        print("  (none)")
        return
    for line in lines:
        print(f"  {line}")


def print_audit(report: AuditReport, *, quiet: bool) -> None:
    _print_section(
        "Unindexed files",
        [str(path) for path in report.unindexed_files],
        quiet=quiet,
    )
    dup_lines = [
        (
            f"repoint book_id={group.book_id} -> {group.keeper}"
            if group.repoint_only
            else f"keeper {group.keeper}  remove {len(group.remove)} file(s)"
            + (f"  book_id={group.book_id}" if group.book_id else "")
        )
        for group in report.duplicate_groups
    ]
    _print_section("Duplicate / moved files", dup_lines, quiet=quiet)
    _print_section(
        "New files to index",
        [str(path) for path in report.new_files],
        quiet=quiet,
    )
    _print_section(
        "Absent database rows",
        [
            f"id={book.id}  {book.file_path}"
            + ("  (missing)" if book.is_missing else "")
            for book in report.absent_books
        ],
        quiet=quiet,
    )
    _print_section(
        "Orphan covers",
        [str(path) for path in report.orphan_covers],
        quiet=quiet,
    )
    _print_section(
        "NULL content_hash rows",
        [f"id={book.id}  {book.file_path}" for book in report.null_hash_books],
        quiet=quiet,
    )
    _print_section(
        "Out-of-root database paths",
        [f"id={book.id}  {book.file_path}" for book in report.out_of_root_books],
        quiet=quiet,
    )
    if report.errors:
        _print_section("Errors", report.errors, quiet=False)


def cmd_audit(args: argparse.Namespace) -> int:
    db_path = _database_path(args)
    scan_roots = _scan_roots(args)
    covers_dir = _covers_dir(args)

    with _connect(db_path) as conn:
        report = audit_library(conn, scan_roots, covers_dir=covers_dir)

    if not args.quiet:
        print(f"Database: {db_path}")
        print(f"Scan roots: {', '.join(str(root) for root in scan_roots)}")
    print_audit(report, quiet=args.quiet)

    if args.force_clean and report.absent_books and not args.execute:
        print(
            f"\nWould purge {len(report.absent_books)} absent row(s) with "
            "--execute --force-clean"
        )

    return 1 if report.errors else 0


def _index_new_files(
    args: argparse.Namespace,
    db_path: Path,
    covers_dir: Path,
    new_files: list[Path],
    result: CleanupResult,
) -> None:
    if not new_files:
        return

    from exlibris.config import resolve_covers_dir
    from exlibris.database import get_engine, init_db
    from exlibris.ebook_meta import EbookMetaError, find_ebook_meta
    from exlibris.scanner import scan_single_file

    try:
        ebook_meta_cmd = find_ebook_meta(None)
    except EbookMetaError as exc:
        result.errors.append(str(exc))
        return

    engine = get_engine(db_path)
    SessionLocal = init_db(engine)
    resolved_covers = resolve_covers_dir(covers_dir)

    with SessionLocal() as session:
        for index, path in enumerate(new_files, start=1):
            try:
                scan_result = scan_single_file(
                    session,
                    path,
                    ebook_meta_cmd=ebook_meta_cmd,
                    covers_dir=resolved_covers,
                    verbose=args.verbose,
                )
                if scan_result.status == "indexed":
                    result.rows_indexed += 1
                    if not args.quiet:
                        title = (
                            scan_result.book.title or scan_result.book.file_name
                            if scan_result.book
                            else path.name
                        )
                        print(f"indexed: {title}")
                elif scan_result.status == "duplicate" and args.verbose:
                    print(f"skipped duplicate during index: {path}")
                elif scan_result.status == "repointed":
                    result.rows_updated += 1
                    if not args.quiet and scan_result.book is not None:
                        title = scan_result.book.title or scan_result.book.file_name
                        print(f"repointed: {title} -> {path.name} (metadata unchanged)")
                elif scan_result.status == "unchanged" and args.verbose:
                    print(f"unchanged during index: {path}")
            except (EbookMetaError, Exception) as exc:
                result.errors.append(f"{path}: {exc}")
            if args.verbose and not args.quiet:
                print(f"[{index}/{len(new_files)}] {path.name}", flush=True)


def cmd_run(args: argparse.Namespace) -> int:
    db_path = _database_path(args)
    scan_roots = _scan_roots(args)
    covers_dir = _covers_dir(args)
    execute = args.execute
    force_clean = args.force_clean

    if force_clean and not execute:
        print(
            "error: --force-clean requires --execute",
            file=sys.stderr,
        )
        return 1

    result = CleanupResult()

    with _connect(db_path) as conn:
        if args.backfill_hashes:
            updated, backfill_errors = backfill_content_hashes(conn, execute=execute)
            result.hashes_backfilled = updated
            result.errors.extend(backfill_errors)
            if not args.quiet:
                verb = "backfilled" if execute else "would backfill"
                print(f"{verb} {updated} content hash(es)")

        report = audit_library(conn, scan_roots, covers_dir=covers_dir)

        if not args.quiet:
            mode = "EXECUTE" if execute else "DRY-RUN"
            print(f"=== {mode} ===")
            print(f"Database: {db_path}")
            print_audit(report, quiet=args.quiet)

        repointed_ids: set[int] = set()

        for group in report.duplicate_groups:
            try:
                rows_updated, files_deleted, repointed_id = apply_duplicate_group(
                    conn, group, scan_roots=scan_roots, execute=execute
                )
                result.rows_updated += rows_updated
                result.files_deleted += files_deleted
                if repointed_id is not None:
                    repointed_ids.add(repointed_id)
                if not args.quiet and (rows_updated or files_deleted):
                    if group.repoint_only and rows_updated:
                        verb = "repoint" if execute else "would repoint"
                        print(
                            f"{verb} book {group.book_id} -> {group.keeper} "
                            "(metadata unchanged)"
                        )
                    elif rows_updated or files_deleted:
                        verb = "dedupe" if execute else "would dedupe"
                        print(
                            f"{verb} hash {group.content_hash[:8]}…: "
                            f"keeper={group.keeper.name}, "
                            f"delete {files_deleted} file(s)"
                            + (
                                f", update book {group.book_id}"
                                if rows_updated
                                else ""
                            )
                        )
            except OSError as exc:
                result.errors.append(str(exc))

        if execute:
            _index_new_files(args, db_path, covers_dir, report.new_files, result)
        elif report.new_files and not args.quiet:
            print(f"would index {len(report.new_files)} new file(s)")

        if args.prune_empty_dirs:
            pruned = prune_empty_directories(scan_roots, execute=execute)
            result.dirs_pruned = pruned
            if not args.quiet and pruned:
                verb = "pruned" if execute else "would prune"
                print(f"{verb} {pruned} empty director{'y' if pruned == 1 else 'ies'}")

        if force_clean:
            valid_ids = {book.id for book in load_books(conn)}
            for book in report.absent_books:
                if book.id in repointed_ids:
                    continue
                if execute:
                    favorites = purge_book(conn, book.id)
                    result.rows_purged += 1
                    valid_ids.discard(book.id)
                    if args.verbose:
                        print(
                            f"purged book id={book.id} "
                            f"({favorites} favorite(s) removed)"
                        )
                elif not args.quiet:
                    print(f"would purge book id={book.id}  {book.file_path}")

            orphan_covers = find_orphan_covers(covers_dir, valid_ids)
            for cover_path in orphan_covers:
                if execute:
                    try:
                        cover_path.unlink()
                        result.covers_removed += 1
                    except OSError as exc:
                        result.errors.append(f"{cover_path}: {exc}")
                else:
                    result.covers_removed += 1

    if not args.quiet:
        print(
            f"\nSummary: "
            f"{result.files_deleted} file(s) deleted, "
            f"{result.rows_updated} row(s) updated, "
            f"{result.rows_indexed} row(s) indexed, "
            f"{result.hashes_backfilled} hash(es) backfilled, "
            f"{result.dirs_pruned} empty dir(s) pruned, "
            f"{result.rows_purged} row(s) purged, "
            f"{result.covers_removed} cover(s) removed"
        )
        if not execute:
            print("Re-run with --execute to apply changes.")

    if result.errors:
        print(f"{len(result.errors)} error(s):", file=sys.stderr)
        for err in result.errors:
            print(f"  - {err}", file=sys.stderr)
        return 1
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit and clean up ExLibris files vs the database.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    audit_parser = subparsers.add_parser(
        "audit",
        help="Report unindexed, duplicate, absent, and orphan items",
    )
    _add_common_args(audit_parser)
    audit_parser.add_argument(
        "--force-clean",
        action="store_true",
        help="Mention rows that would be purged (still read-only)",
    )
    audit_parser.add_argument(
        "--execute",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    audit_parser.set_defaults(func=cmd_audit)

    run_parser = subparsers.add_parser(
        "run",
        help="Deduplicate files, index new EPUBs, optionally purge absent rows",
    )
    _add_common_args(run_parser)
    run_parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply changes (default is dry-run)",
    )
    run_parser.add_argument(
        "--force-clean",
        action="store_true",
        help="With --execute, hard-delete DB rows whose file is gone",
    )
    run_parser.add_argument(
        "--backfill-hashes",
        action="store_true",
        help="Compute SHA-1 for rows with NULL content_hash",
    )
    run_parser.add_argument(
        "--prune-empty-dirs",
        action="store_true",
        help="Remove empty directories under scan roots",
    )
    run_parser.set_defaults(func=cmd_run)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
