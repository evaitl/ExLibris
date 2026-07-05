#!/usr/bin/env python3
"""Rename EPUB files with short basenames using title, author, and publisher metadata."""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_VENV_PYTHON = _ROOT / ".venv" / "bin" / "python"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from exlibris.book_paths import path_is_under_any_root
from exlibris.cleanup import update_book_path

_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00]')
_WHITESPACE = re.compile(r"\s+")


def _ensure_project_python() -> None:
    if os.environ.get("EXLIBRIS_REEXEC") == "1":
        return
    try:
        import pydantic  # noqa: F401
    except ModuleNotFoundError:
        if _VENV_PYTHON.is_file():
            os.environ["EXLIBRIS_REEXEC"] = "1"
            os.execv(str(_VENV_PYTHON), [str(_VENV_PYTHON), *sys.argv])
        print(
            "error: run from the project venv:\n"
            "  .venv/bin/python scripts/rename-short-files.py",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


def _resolve_project_path(path: Path) -> Path:
    path = path.expanduser()
    if not path.is_absolute():
        path = _ROOT / path
    return path.resolve()


def _load_yaml_config(config: Path | None) -> dict:
    path = config.expanduser() if config else _ROOT / "config.yaml"
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


def _scan_roots(args: argparse.Namespace) -> list[Path]:
    if args.path:
        return [_resolve_project_path(path) for path in args.path]
    data = _load_yaml_config(args.config)
    raw_paths = data.get("scan_paths")
    if isinstance(raw_paths, list) and raw_paths:
        return [_resolve_project_path(Path(str(path))) for path in raw_paths]
    return [Path("/media/books").resolve()]


def _connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        raise FileNotFoundError(f"database not found: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _clean_field(value: object | None, *, fallback: str) -> str:
    if value is None:
        return fallback
    text = _WHITESPACE.sub(" ", str(value).strip())
    return text or fallback


def sanitize_filename(name: str, *, max_stem_len: int = 180) -> str:
    cleaned = _INVALID_FILENAME_CHARS.sub("", name)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip().rstrip(".")
    path = Path(cleaned)
    stem = path.stem
    suffix = path.suffix or ".epub"
    if len(stem) > max_stem_len:
        stem = stem[:max_stem_len].rstrip()
    return f"{stem}{suffix}"


def build_target_file_name(
    *,
    title: str | None,
    authors: str | None,
    publisher: str | None,
    fallback_stem: str,
) -> str:
    title_text = _clean_field(title, fallback=fallback_stem)
    authors_text = _clean_field(authors, fallback="Unknown Author")
    publisher_text = _clean_field(publisher, fallback="Unknown Publisher")
    raw = f"{title_text} - {authors_text}-({publisher_text}).epub"
    return sanitize_filename(raw)


def has_short_basename(file_name: str, *, max_stem_len: int) -> bool:
    if Path(file_name).suffix.lower() != ".epub":
        return False
    return len(Path(file_name).stem) < max_stem_len


def unique_target_path(directory: Path, file_name: str) -> Path:
    candidate = directory / file_name
    if not candidate.exists():
        return candidate
    stem = Path(file_name).stem
    suffix = Path(file_name).suffix
    for index in range(2, 10_000):
        candidate = directory / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
    raise OSError(f"could not find unused filename in {directory}")


@dataclass(frozen=True)
class RenameCandidate:
    book_id: int
    old_path: Path
    new_path: Path
    old_name: str
    new_name: str


def load_rename_candidates(
    conn: sqlite3.Connection,
    *,
    max_stem_len: int,
) -> list[RenameCandidate]:
    rows = conn.execute(
        """
        SELECT id, file_path, file_name, title, authors, publisher
        FROM books
        WHERE is_missing = 0
        ORDER BY id
        """
    ).fetchall()

    candidates: list[RenameCandidate] = []
    for row in rows:
        file_name = str(row["file_name"])
        if not has_short_basename(file_name, max_stem_len=max_stem_len):
            continue

        old_path = Path(str(row["file_path"]))
        new_name = build_target_file_name(
            title=row["title"],
            authors=row["authors"],
            publisher=row["publisher"],
            fallback_stem=Path(file_name).stem,
        )
        if new_name == file_name:
            continue

        new_path = unique_target_path(old_path.parent, new_name)
        candidates.append(
            RenameCandidate(
                book_id=int(row["id"]),
                old_path=old_path,
                new_path=new_path,
                old_name=file_name,
                new_name=new_path.name,
            )
        )
    return candidates


def apply_rename(
    conn: sqlite3.Connection,
    candidate: RenameCandidate,
    *,
    scan_roots: list[Path],
    execute: bool,
) -> str | None:
    old_path = candidate.old_path.resolve()
    new_path = candidate.new_path.resolve()

    if old_path == new_path:
        return None

    if not old_path.is_file():
        return f"id={candidate.book_id}: file not found: {old_path}"

    if not path_is_under_any_root(old_path, scan_roots):
        return f"id={candidate.book_id}: refusing to rename outside scan roots: {old_path}"

    if not path_is_under_any_root(new_path.parent, scan_roots):
        return f"id={candidate.book_id}: refusing to write outside scan roots: {new_path}"

    if new_path.exists() and new_path != old_path:
        return f"id={candidate.book_id}: target already exists: {new_path}"

    if execute:
        new_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.rename(new_path)
        update_book_path(conn, candidate.book_id, new_path)

    return None


def main(argv: list[str] | None = None) -> int:
    _ensure_project_python()

    parser = argparse.ArgumentParser(
        description=(
            "Rename EPUB files whose basename (without .epub) is shorter than "
            "N characters to '{title} - {authors}-({publisher}).epub' and "
            "update the database."
        )
    )
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
        "--max-stem-len",
        type=int,
        default=10,
        help="Rename when file_name stem length is below this value (default: 10)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Apply renames and database updates (default: dry run)",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Only print errors and summary",
    )
    args = parser.parse_args(argv)

    if args.max_stem_len < 1:
        parser.error("--max-stem-len must be at least 1")

    db_path = _database_path(args)
    scan_roots = _scan_roots(args)

    renamed = 0
    skipped = 0
    errors: list[str] = []

    with _connect(db_path) as conn:
        candidates = load_rename_candidates(conn, max_stem_len=args.max_stem_len)

        if not args.quiet:
            mode = "EXECUTE" if args.execute else "DRY RUN"
            print(f"Database: {db_path}")
            print(f"Scan roots: {', '.join(str(root) for root in scan_roots)}")
            print(f"Mode: {mode}")
            print(f"Candidates: {len(candidates)}")

        for candidate in candidates:
            error = apply_rename(
                conn,
                candidate,
                scan_roots=scan_roots,
                execute=args.execute,
            )
            if error:
                errors.append(error)
                skipped += 1
                if not args.quiet:
                    print(f"skip  {error}")
                continue

            renamed += 1
            action = "rename" if args.execute else "would rename"
            if not args.quiet:
                print(
                    f"{action} id={candidate.book_id}\n"
                    f"  from: {candidate.old_path}\n"
                    f"    to: {candidate.new_path}"
                )

    if not args.quiet:
        print(
            f"\nSummary: {renamed} {'renamed' if args.execute else 'to rename'}, "
            f"{skipped} skipped, {len(errors)} error(s)"
        )
    for error in errors:
        print(error, file=sys.stderr)

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
