"""Tests for SQLite engine setup and writable-database checks."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from exlibris.database import (
    DatabaseNotWritableError,
    database_not_writable_message,
    get_engine,
    is_readonly_sqlite_error,
)


def test_is_readonly_sqlite_error() -> None:
    assert is_readonly_sqlite_error(
        sqlite3.OperationalError("attempt to write a readonly database")
    )
    assert is_readonly_sqlite_error(
        sqlite3.OperationalError("unable to open database file")
    )
    assert not is_readonly_sqlite_error(sqlite3.OperationalError("database is locked"))


def test_get_engine_creates_writable_database() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "library.db"
        engine = get_engine(db)
        try:
            assert db.is_file()
            assert os.access(db, os.W_OK)
        finally:
            engine.dispose()


def test_get_engine_readonly_file_raises_helpful_error() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "library.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.close()
        db.chmod(0o444)
        try:
            with pytest.raises(DatabaseNotWritableError) as excinfo:
                get_engine(db)
        finally:
            db.chmod(0o644)
        message = str(excinfo.value)
        assert str(db) in message
        assert "library.lock" in message
        assert "not writable" in message
        assert "sudo chown" in message


def test_get_engine_readonly_directory_raises() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        parent = Path(tmp) / "data"
        parent.mkdir()
        db = parent / "library.db"
        sqlite3.connect(db).close()
        parent.chmod(0o555)
        try:
            with pytest.raises(DatabaseNotWritableError) as excinfo:
                get_engine(db)
            assert "not writable" in str(excinfo.value)
        finally:
            parent.chmod(0o755)


def test_message_mentions_unwritable_wal_sidecar() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "library.db"
        db.write_bytes(b"")
        wal = Path(str(db) + "-wal")
        wal.write_bytes(b"")
        wal.chmod(0o444)
        try:
            message = database_not_writable_message(db)
        finally:
            wal.chmod(0o644)
        assert "library.db-wal" in message
        assert "not writable" in message
