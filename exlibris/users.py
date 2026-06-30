from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from exlibris.auth import hash_password


class UserError(Exception):
    pass


@dataclass(frozen=True)
class UserRecord:
    id: int
    username: str
    created_at: str
    favorite_count: int


def normalize_username(username: str) -> str:
    return username.strip()


def validate_registration(
    username: str,
    password: str,
    *,
    password_confirm: str | None = None,
) -> str:
    cleaned = normalize_username(username)
    if not cleaned:
        raise UserError("Username cannot be empty.")
    if len(cleaned) > 64:
        raise UserError("Username must be 64 characters or fewer.")
    if not password:
        raise UserError("Password cannot be empty.")
    if password_confirm is not None and password != password_confirm:
        raise UserError("Passwords do not match.")
    return cleaned


def register_user(
    conn: sqlite3.Connection,
    *,
    username: str,
    password: str,
    password_confirm: str | None = None,
) -> tuple[int, str]:
    """Create an account. Returns (user_id, username). Password is stored as a scrypt hash."""
    cleaned = validate_registration(
        username, password, password_confirm=password_confirm
    )
    existing = conn.execute(
        "SELECT 1 FROM users WHERE username = ? COLLATE NOCASE",
        (cleaned,),
    ).fetchone()
    if existing is not None:
        raise UserError("That username is already taken.")

    now = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        """
        INSERT INTO users (username, password_hash, created_at)
        VALUES (?, ?, ?)
        """,
        (cleaned, hash_password(password), now),
    )
    conn.commit()
    user_id = int(cursor.lastrowid)
    return user_id, cleaned


def list_users(conn: sqlite3.Connection) -> list[UserRecord]:
    """Return all accounts, ordered by username."""
    rows = conn.execute(
        """
        SELECT u.id, u.username, u.created_at, COUNT(f.book_id) AS favorite_count
        FROM users u
        LEFT JOIN user_favorites f ON f.user_id = u.id
        GROUP BY u.id
        ORDER BY u.username COLLATE NOCASE
        """
    ).fetchall()
    return [
        UserRecord(
            id=int(row["id"]),
            username=str(row["username"]),
            created_at=str(row["created_at"]),
            favorite_count=int(row["favorite_count"]),
        )
        for row in rows
    ]


def delete_user(conn: sqlite3.Connection, *, username: str) -> str:
    """Delete an account by username. Favorites are removed via CASCADE."""
    cleaned = normalize_username(username)
    if not cleaned:
        raise UserError("Username cannot be empty.")

    row = conn.execute(
        "SELECT id, username FROM users WHERE username = ? COLLATE NOCASE",
        (cleaned,),
    ).fetchone()
    if row is None:
        raise UserError(f"No user named {cleaned!r}.")

    deleted_username = str(row["username"])
    conn.execute("DELETE FROM users WHERE id = ?", (int(row["id"]),))
    conn.commit()
    return deleted_username
