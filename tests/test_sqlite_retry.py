"""Tests for SQLite lock retry helpers."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from exlibris.sqlite_retry import (
    is_sqlite_locked,
    run_write_with_retry,
)


def test_is_sqlite_locked() -> None:
    assert is_sqlite_locked(sqlite3.OperationalError("database is locked"))
    assert is_sqlite_locked(sqlite3.OperationalError("database is busy"))
    assert not is_sqlite_locked(sqlite3.OperationalError("no such table: books"))
    assert not is_sqlite_locked(ValueError("database is locked"))


def test_run_write_with_retry_retries_then_succeeds() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = sqlite3.connect(Path(tmp) / "library.db")
        conn.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, description TEXT)")
        attempts = {"count": 0}

        def writer() -> None:
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise sqlite3.OperationalError("database is locked")
            conn.execute(
                "INSERT INTO books (id, description) VALUES (1, 'plain')"
            )

        with patch("exlibris.sqlite_retry.time.sleep"):
            run_write_with_retry(conn, writer, max_attempts=3)

        row = conn.execute(
            "SELECT description FROM books WHERE id = 1"
        ).fetchone()
        assert row[0] == "plain"
        assert attempts["count"] == 2
        conn.close()


def test_run_write_with_retry_raises_after_max_attempts() -> None:
    conn = sqlite3.connect(":memory:")

    def writer() -> None:
        raise sqlite3.OperationalError("database is locked")

    with patch("exlibris.sqlite_retry.time.sleep"):
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            run_write_with_retry(conn, writer, max_attempts=2)
    conn.close()
