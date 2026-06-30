#!/usr/bin/env python3
"""List or delete ExLibris web user accounts."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

from exlibris.users import UserError, delete_user, list_users

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


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _add_db_args(parser: argparse.ArgumentParser) -> None:
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


def cmd_list(args: argparse.Namespace) -> int:
    db_path = _database_path(args)
    try:
        with _connect(db_path) as conn:
            users = list_users(conn)
    except sqlite3.OperationalError as exc:
        if "no such table: users" in str(exc).lower():
            print("No user accounts table yet. Run 'exlibris scan' or create a user first.")
            return 1
        raise

    if not users:
        print("No user accounts.")
        return 0

    id_w = max(len(str(user.id)) for user in users)
    name_w = max(len(user.username) for user in users)
    fav_w = max(len(str(user.favorite_count)) for user in users)
    print(f"{'ID':>{id_w}}  {'Username':<{name_w}}  {'Favorites':>{fav_w}}  Created")
    for user in users:
        print(
            f"{user.id:>{id_w}}  {user.username:<{name_w}}  "
            f"{user.favorite_count:>{fav_w}}  {user.created_at}"
        )
    print(f"\n{len(users)} account(s) in {db_path}.")
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    username = args.username.strip()
    if not username:
        print("error: username cannot be empty", file=sys.stderr)
        return 1

    if not args.yes:
        prompt = f"Delete user {username!r} and all favorites? [y/N] "
        answer = input(prompt).strip().lower()
        if answer not in ("y", "yes"):
            print("Cancelled.")
            return 0

    db_path = _database_path(args)
    try:
        with _connect(db_path) as conn:
            deleted = delete_user(conn, username=username)
    except UserError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except sqlite3.OperationalError as exc:
        if "no such table: users" in str(exc).lower():
            print("No user accounts table yet.", file=sys.stderr)
            return 1
        raise

    print(f"Deleted user {deleted!r}.")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List or delete ExLibris web user accounts.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="Show all accounts")
    _add_db_args(list_parser)
    list_parser.set_defaults(func=cmd_list)

    delete_parser = subparsers.add_parser("delete", help="Remove an account")
    _add_db_args(delete_parser)
    delete_parser.add_argument("username", help="Username to delete")
    delete_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Delete without confirmation",
    )
    delete_parser.set_defaults(func=cmd_delete)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
