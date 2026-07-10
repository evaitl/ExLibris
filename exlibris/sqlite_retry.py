"""Retry helpers for SQLite database lock contention."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable

DEFAULT_BUSY_TIMEOUT_MS = 30_000
DEFAULT_MAX_ATTEMPTS = 20
DEFAULT_INITIAL_DELAY_S = 0.1
DEFAULT_MAX_DELAY_S = 2.0


def is_sqlite_locked(exc: BaseException) -> bool:
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    message = str(exc).lower()
    return "database is locked" in message or "database is busy" in message


def configure_sqlite_connection(
    conn: sqlite3.Connection,
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> None:
    """Apply pragmas for safer concurrent access."""
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        pass


def commit_with_retry(
    conn: sqlite3.Connection,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    initial_delay_s: float = DEFAULT_INITIAL_DELAY_S,
    max_delay_s: float = DEFAULT_MAX_DELAY_S,
) -> None:
    """Commit, retrying when SQLite reports the database is locked or busy."""
    delay = initial_delay_s
    for attempt in range(max_attempts):
        try:
            conn.commit()
            return
        except sqlite3.OperationalError as exc:
            if not is_sqlite_locked(exc) or attempt == max_attempts - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, max_delay_s)


def run_write_with_retry(
    conn: sqlite3.Connection,
    writer: Callable[[], None],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    initial_delay_s: float = DEFAULT_INITIAL_DELAY_S,
    max_delay_s: float = DEFAULT_MAX_DELAY_S,
) -> None:
    """Run a write callback and commit, retrying on database lock contention."""
    delay = initial_delay_s
    for attempt in range(max_attempts):
        try:
            writer()
            conn.commit()
            return
        except sqlite3.OperationalError as exc:
            conn.rollback()
            if not is_sqlite_locked(exc) or attempt == max_attempts - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, max_delay_s)
