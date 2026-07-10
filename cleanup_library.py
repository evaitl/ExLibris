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
    EpubRemovalContext,
    EpubRemovalTotals,
    apply_duplicate_group,
    audit_epub_integrity,
    audit_library,
    backfill_content_hashes,
    build_path_to_book_id,
    collect_epub_paths_for_validation,
    find_orphan_covers,
    list_filename_fixes,
    load_books,
    purge_book,
    sanitize_book_filenames,
    strip_book_descriptions,
)
from exlibris.book_paths import prune_empty_directories
from exlibris.sqlite_retry import configure_sqlite_connection

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
    configure_sqlite_connection(conn)
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


def _log(message: str = "", *, file=None) -> None:
    print(message, file=file, flush=True)


def _print_section(title: str, lines: list[str], *, quiet: bool) -> None:
    if quiet and not lines:
        return
    _log(f"\n== {title} ({len(lines)}) ==")
    if not lines:
        _log("  (none)")
        return
    for line in lines:
        _log(f"  {line}")


def _progress_callback(*, quiet: bool, verbose: bool, label: str):
    """Return a progress callback, or None when output should stay quiet."""
    if quiet:
        return None

    def on_progress(current: int, total: int, item: str) -> None:
        if total <= 0:
            return
        width = len(str(total))
        if verbose or current == 1 or current == total or current % 25 == 0:
            _log(f"{label} [{current:>{width}}/{total}] {item}")

    return on_progress


def _epub_validation_live_callbacks(*, quiet: bool):
    """Callbacks that stream invalid EPUBs and valid milestones during validation."""
    if quiet:
        return None, None

    def on_invalid(item) -> None:
        _log(f"invalid: {item.display_line()}")

    def on_valid_progress(valid_count: int, total: int) -> None:
        width = len(str(total))
        _log(f"valid [{valid_count:>{width}}/{total}] checked OK")

    return on_invalid, on_valid_progress


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
    _print_section("Filename fixes", report.filename_fixes, quiet=quiet)
    _print_section("Invalid EPUBs", report.invalid_epubs, quiet=quiet)
    if report.errors:
        _print_section("Errors", report.errors, quiet=False)


def _validate_epubs(
    conn: sqlite3.Connection,
    scan_roots: list[Path],
    *,
    deep: bool,
    ebook_meta: str | None,
    quiet: bool = False,
    verbose: bool = False,
) -> tuple[list[str], list[str]]:
    paths, skipped = collect_epub_paths_for_validation(
        conn, scan_roots, deep=deep
    )
    if not quiet:
        if skipped:
            _log(f"Skipping {skipped} already-validated EPUB(s)")
        if paths:
            _log(f"Validating {len(paths)} EPUB(s)...")
    on_invalid, on_valid_progress = _epub_validation_live_callbacks(quiet=quiet)
    invalid, errors = audit_epub_integrity(
        paths,
        path_to_book_id=build_path_to_book_id(conn),
        deep=deep,
        ebook_meta_cmd=ebook_meta,
        conn=conn,
        on_invalid=on_invalid,
        on_valid_progress=on_valid_progress,
    )
    return [item.display_line() for item in invalid], errors


def _run_epub_validation_pass(
    conn: sqlite3.Connection,
    scan_roots: list[Path],
    covers_dir: Path,
    *,
    deep: bool,
    ebook_meta: str | None,
    execute: bool,
    quiet: bool = False,
    verbose: bool = False,
) -> tuple[CleanupResult, list[str]]:
    """Validate EPUBs and optionally remove invalid files/rows."""
    result = CleanupResult()
    paths, skipped = collect_epub_paths_for_validation(
        conn, scan_roots, deep=deep
    )
    if not quiet:
        if skipped:
            _log(f"Skipping {skipped} already-validated EPUB(s)")
        if paths:
            _log(f"Validating {len(paths)} EPUB(s)...")
    on_invalid, on_valid_progress = _epub_validation_live_callbacks(quiet=quiet)
    removal_totals = EpubRemovalTotals()
    invalid_items, validate_errors = audit_epub_integrity(
        paths,
        path_to_book_id=build_path_to_book_id(conn),
        deep=deep,
        ebook_meta_cmd=ebook_meta,
        conn=conn,
        removal=EpubRemovalContext(
            scan_roots=scan_roots,
            covers_dir=covers_dir,
            execute=execute,
        ),
        removal_totals=removal_totals,
        on_invalid=on_invalid,
        on_valid_progress=on_valid_progress,
    )
    invalid_lines = [item.display_line() for item in invalid_items]
    result.invalid_epubs = len(invalid_items)
    result.errors.extend(validate_errors)
    result.files_deleted += removal_totals.files_deleted
    result.rows_purged += removal_totals.rows_purged
    result.covers_removed += removal_totals.covers_removed

    return result, invalid_lines


def _normalize_validate_epub_args(args: argparse.Namespace) -> int | None:
    if args.validate_epubs_only:
        args.validate_epubs = True
    if args.validate_epubs_deep and not args.validate_epubs:
        _log(
            "error: --validate-epubs-deep requires --validate-epubs",
            file=sys.stderr,
        )
        return 1
    if args.validate_epubs_only and not args.validate_epubs:
        _log(
            "error: --validate-epubs-only requires EPUB validation",
            file=sys.stderr,
        )
        return 1
    return None


def _print_epub_validation_only(
    invalid_lines: list[str],
    result: CleanupResult,
    *,
    db_path: Path,
    scan_roots: list[Path],
    quiet: bool,
    execute: bool,
) -> None:
    if not quiet:
        mode = "EXECUTE" if execute else "DRY-RUN"
        _log(f"=== {mode} (EPUB validation only) ===")
        _log(f"Database: {db_path}")
        _log(f"Scan roots: {', '.join(str(root) for root in scan_roots)}")
    _print_section("Invalid EPUBs", invalid_lines, quiet=quiet)
    if not quiet and result.invalid_epubs:
        if execute:
            _log(
                f"removed {result.invalid_epubs} invalid EPUB(s): "
                f"{result.files_deleted} file(s), {result.rows_purged} row(s), "
                f"{result.covers_removed} cover set(s)"
            )
        else:
            _log(f"would remove {result.invalid_epubs} invalid EPUB(s)")
    if not quiet:
        _log(
            f"\nSummary: "
            f"{result.invalid_epubs} invalid EPUB(s), "
            f"{result.files_deleted} file(s) deleted, "
            f"{result.rows_purged} row(s) purged, "
            f"{result.covers_removed} cover(s) removed"
        )
        if not execute:
            _log("Re-run with --execute to apply changes.")


def cmd_audit(args: argparse.Namespace) -> int:
    if (code := _normalize_validate_epub_args(args)) is not None:
        return code

    db_path = _database_path(args)
    scan_roots = _scan_roots(args)
    covers_dir = _covers_dir(args)

    if not args.quiet:
        _log(f"Database: {db_path}")
        _log(f"Scan roots: {', '.join(str(root) for root in scan_roots)}")

    with _connect(db_path) as conn:
        if args.validate_epubs_only:
            result, invalid_lines = _run_epub_validation_pass(
                conn,
                scan_roots,
                covers_dir,
                deep=args.validate_epubs_deep,
                ebook_meta=args.ebook_meta,
                execute=False,
                quiet=args.quiet,
                verbose=args.verbose,
            )
            _print_epub_validation_only(
                invalid_lines,
                result,
                db_path=db_path,
                scan_roots=scan_roots,
                quiet=args.quiet,
                execute=False,
            )
            if result.errors:
                _log(f"{len(result.errors)} error(s):", file=sys.stderr)
                for err in result.errors:
                    _log(f"  - {err}", file=sys.stderr)
                return 1
            return 0

        if not args.quiet:
            _log("Auditing library...")
        report = audit_library(
            conn,
            scan_roots,
            covers_dir=covers_dir,
            on_progress=_progress_callback(
                quiet=args.quiet, verbose=args.verbose, label="hash"
            ),
        )
        if args.validate_epubs:
            invalid, validate_errors = _validate_epubs(
                conn,
                scan_roots,
                deep=args.validate_epubs_deep,
                ebook_meta=args.ebook_meta,
                quiet=args.quiet,
                verbose=args.verbose,
            )
            report.invalid_epubs = invalid
            report.errors.extend(validate_errors)

    print_audit(report, quiet=args.quiet)

    if args.force_clean and report.absent_books and not args.execute:
        _log(
            f"\nWould purge {len(report.absent_books)} absent row(s) with "
            "--execute --force-clean"
        )

    return 1 if report.errors else 0


def _index_new_files(
    args: argparse.Namespace,
    db_path: Path,
    covers_dir: Path,
    scan_roots: list[Path],
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

    if not args.quiet:
        _log(f"Indexing {len(new_files)} new file(s)...")

    with SessionLocal() as session:
        for index, path in enumerate(new_files, start=1):
            try:
                scan_result = scan_single_file(
                    session,
                    path,
                    ebook_meta_cmd=ebook_meta_cmd,
                    covers_dir=resolved_covers,
                    verbose=args.verbose,
                    scan_roots=scan_roots,
                    validate_epub=True,
                )
                if scan_result.status == "indexed":
                    result.rows_indexed += 1
                    if not args.quiet:
                        title = (
                            scan_result.book.title or scan_result.book.file_name
                            if scan_result.book
                            else path.name
                        )
                        _log(f"indexed: {title}")
                elif scan_result.status == "duplicate" and args.verbose:
                    _log(f"skipped duplicate during index: {path}")
                elif scan_result.status == "repointed":
                    result.rows_updated += 1
                    if not args.quiet and scan_result.book is not None:
                        title = scan_result.book.title or scan_result.book.file_name
                        _log(
                            f"repointed: {title} -> {path.name} (metadata unchanged)"
                        )
                elif scan_result.status == "unchanged" and args.verbose:
                    _log(f"unchanged during index: {path}")
                elif scan_result.status == "invalid_epub":
                    result.invalid_epubs += 1
                    result.files_deleted += scan_result.files_deleted
                    detail = scan_result.detail or "invalid EPUB"
                    result.errors.append(f"{path}: {detail}")
                    if not args.quiet:
                        _log(f"invalid EPUB: {path.name}")
                        if scan_result.files_deleted:
                            _log(f"deleted invalid EPUB: {path}")
            except (EbookMetaError, Exception) as exc:
                result.errors.append(f"{path}: {exc}")
            if not args.quiet and (
                args.verbose
                or index == 1
                or index == len(new_files)
                or index % 25 == 0
            ):
                width = len(str(len(new_files)))
                _log(f"index [{index:>{width}}/{len(new_files)}] {path.name}")


def cmd_run(args: argparse.Namespace) -> int:
    if (code := _normalize_validate_epub_args(args)) is not None:
        return code

    db_path = _database_path(args)
    scan_roots = _scan_roots(args)
    covers_dir = _covers_dir(args)
    execute = args.execute
    force_clean = args.force_clean

    if force_clean and not execute:
        _log(
            "error: --force-clean requires --execute",
            file=sys.stderr,
        )
        return 1

    result = CleanupResult()

    if not args.quiet:
        mode = "EXECUTE" if execute else "DRY-RUN"
        _log(f"=== {mode} ===")
        _log(f"Database: {db_path}")
        _log(f"Scan roots: {', '.join(str(root) for root in scan_roots)}")

    with _connect(db_path) as conn:
        if args.validate_epubs_only:
            result, invalid_lines = _run_epub_validation_pass(
                conn,
                scan_roots,
                covers_dir,
                deep=args.validate_epubs_deep,
                ebook_meta=args.ebook_meta,
                execute=execute,
                quiet=args.quiet,
                verbose=args.verbose,
            )
            _print_epub_validation_only(
                invalid_lines,
                result,
                db_path=db_path,
                scan_roots=scan_roots,
                quiet=args.quiet,
                execute=execute,
            )
            if result.errors:
                _log(f"{len(result.errors)} error(s):", file=sys.stderr)
                for err in result.errors:
                    _log(f"  - {err}", file=sys.stderr)
                return 1
            return 0

        if args.backfill_hashes:
            if not args.quiet:
                _log("Backfilling content hashes...")
            updated, backfill_errors = backfill_content_hashes(
                conn,
                execute=execute,
                on_progress=_progress_callback(
                    quiet=args.quiet, verbose=args.verbose, label="backfill"
                ),
            )
            result.hashes_backfilled = updated
            result.errors.extend(backfill_errors)
            if not args.quiet:
                verb = "backfilled" if execute else "would backfill"
                _log(f"{verb} {updated} content hash(es)")

        if args.strip_description_html:
            if not args.quiet:
                _log("Stripping HTML from book descriptions...")
            stripped, strip_errors = strip_book_descriptions(
                conn,
                execute=execute,
                on_progress=_progress_callback(
                    quiet=args.quiet,
                    verbose=args.verbose,
                    label="description",
                ),
            )
            result.descriptions_stripped = stripped
            result.errors.extend(strip_errors)
            if not args.quiet:
                verb = "stripped" if execute else "would strip"
                _log(f"{verb} {stripped} description(s)")

        if not args.quiet:
            _log("Sanitizing filenames...")
        sanitized, sanitize_errors = sanitize_book_filenames(
            conn,
            scan_roots,
            execute=execute,
            on_progress=_progress_callback(
                quiet=args.quiet, verbose=args.verbose, label="sanitize"
            ),
        )
        result.filenames_sanitized = sanitized
        result.errors.extend(sanitize_errors)
        if not args.quiet and sanitized:
            verb = "sanitized" if execute else "would sanitize"
            _log(f"{verb} {sanitized} filename(s)")
        elif not args.quiet and not execute:
            fixes = list_filename_fixes(conn, scan_roots)
            if fixes and args.verbose:
                for line in fixes:
                    _log(f"would rename: {line}")

        if not args.quiet:
            _log("Auditing library...")
        report = audit_library(
            conn,
            scan_roots,
            covers_dir=covers_dir,
            on_progress=_progress_callback(
                quiet=args.quiet, verbose=args.verbose, label="hash"
            ),
        )

        invalid_items = []
        if args.validate_epubs:
            paths, skipped = collect_epub_paths_for_validation(
                conn,
                scan_roots,
                deep=args.validate_epubs_deep,
            )
            if not args.quiet:
                if skipped:
                    _log(f"Skipping {skipped} already-validated EPUB(s)")
                if paths:
                    _log(f"Validating {len(paths)} EPUB(s)...")
            on_invalid, on_valid_progress = _epub_validation_live_callbacks(
                quiet=args.quiet
            )
            removal_totals = EpubRemovalTotals()
            invalid_items, validate_errors = audit_epub_integrity(
                paths,
                path_to_book_id=build_path_to_book_id(conn),
                deep=args.validate_epubs_deep,
                ebook_meta_cmd=args.ebook_meta,
                conn=conn,
                removal=EpubRemovalContext(
                    scan_roots=scan_roots,
                    covers_dir=covers_dir,
                    execute=execute,
                ),
                removal_totals=removal_totals,
                on_invalid=on_invalid,
                on_valid_progress=on_valid_progress,
            )
            report.invalid_epubs = [item.display_line() for item in invalid_items]
            result.invalid_epubs = len(invalid_items)
            result.errors.extend(validate_errors)
            result.files_deleted += removal_totals.files_deleted
            result.rows_purged += removal_totals.rows_purged
            result.covers_removed += removal_totals.covers_removed

        if not args.quiet:
            print_audit(report, quiet=args.quiet)

        if args.validate_epubs and invalid_items and not args.quiet:
            if execute:
                _log(
                    f"removed {len(invalid_items)} invalid EPUB(s): "
                    f"{removal_totals.files_deleted} file(s), "
                    f"{removal_totals.rows_purged} row(s), "
                    f"{removal_totals.covers_removed} cover set(s)"
                )
            else:
                _log(f"would remove {len(invalid_items)} invalid EPUB(s)")

        repointed_ids: set[int] = set()

        if report.duplicate_groups and not args.quiet:
            _log(f"Processing {len(report.duplicate_groups)} duplicate group(s)...")

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
                        _log(
                            f"{verb} book {group.book_id} -> {group.keeper} "
                            "(metadata unchanged)"
                        )
                    elif rows_updated or files_deleted:
                        verb = "dedupe" if execute else "would dedupe"
                        _log(
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
            _index_new_files(
                args, db_path, covers_dir, scan_roots, report.new_files, result
            )
        elif report.new_files and not args.quiet:
            _log(f"would index {len(report.new_files)} new file(s)")

        if args.prune_empty_dirs:
            if not args.quiet:
                _log("Pruning empty directories...")
            pruned = prune_empty_directories(scan_roots, execute=execute)
            result.dirs_pruned = pruned
            if not args.quiet and pruned:
                verb = "pruned" if execute else "would prune"
                _log(f"{verb} {pruned} empty director{'y' if pruned == 1 else 'ies'}")

        if force_clean:
            if not args.quiet:
                _log("Purging absent rows / orphan covers...")
            valid_ids = {book.id for book in load_books(conn)}
            for book in report.absent_books:
                if book.id in repointed_ids:
                    continue
                if execute:
                    favorites = purge_book(conn, book.id)
                    result.rows_purged += 1
                    valid_ids.discard(book.id)
                    if args.verbose:
                        _log(
                            f"purged book id={book.id} "
                            f"({favorites} favorite(s) removed)"
                        )
                elif not args.quiet:
                    _log(f"would purge book id={book.id}  {book.file_path}")

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
        _log(
            f"\nSummary: "
            f"{result.files_deleted} file(s) deleted, "
            f"{result.rows_updated} row(s) updated, "
            f"{result.rows_indexed} row(s) indexed, "
            f"{result.hashes_backfilled} hash(es) backfilled, "
            f"{result.filenames_sanitized} filename(s) sanitized, "
            f"{result.descriptions_stripped} description(s) stripped, "
            f"{result.invalid_epubs} invalid EPUB(s), "
            f"{result.dirs_pruned} empty dir(s) pruned, "
            f"{result.rows_purged} row(s) purged, "
            f"{result.covers_removed} cover(s) removed"
        )
        if not execute:
            _log("Re-run with --execute to apply changes.")

    if result.errors:
        _log(f"{len(result.errors)} error(s):", file=sys.stderr)
        for err in result.errors:
            _log(f"  - {err}", file=sys.stderr)
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
        "--validate-epubs",
        action="store_true",
        help="Check EPUB ZIP/OPF/spine structure (and readability of spine HTML)",
    )
    audit_parser.add_argument(
        "--validate-epubs-only",
        action="store_true",
        help="Skip other audit checks; only validate EPUB structure",
    )
    audit_parser.add_argument(
        "--validate-epubs-deep",
        action="store_true",
        help="With --validate-epubs, also require Calibre ebook-meta to open the file",
    )
    audit_parser.add_argument(
        "--ebook-meta",
        type=str,
        default=None,
        help="Path to Calibre ebook-meta (for --validate-epubs-deep)",
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
        "--strip-description-html",
        action="store_true",
        help="Remove HTML tags and decode HTML entities in book descriptions",
    )
    run_parser.add_argument(
        "--prune-empty-dirs",
        action="store_true",
        help="Remove empty directories under scan roots",
    )
    run_parser.add_argument(
        "--validate-epubs",
        action="store_true",
        help="Check EPUB ZIP/OPF/spine structure (and readability of spine HTML)",
    )
    run_parser.add_argument(
        "--validate-epubs-only",
        action="store_true",
        help="Skip dedupe, indexing, and other cleanup; only validate EPUBs",
    )
    run_parser.add_argument(
        "--validate-epubs-deep",
        action="store_true",
        help="With --validate-epubs, also require Calibre ebook-meta to open the file",
    )
    run_parser.add_argument(
        "--ebook-meta",
        type=str,
        default=None,
        help="Path to Calibre ebook-meta (for --validate-epubs-deep)",
    )
    run_parser.set_defaults(func=cmd_run)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
