from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from exlibris.auth import hash_password


class UserError(Exception):
    pass


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
