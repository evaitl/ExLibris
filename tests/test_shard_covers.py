"""Integration test for shard-covers migration script."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path


def _init_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE books (
            id INTEGER PRIMARY KEY,
            file_path TEXT NOT NULL UNIQUE,
            file_name TEXT NOT NULL,
            cover_path TEXT
        );
        INSERT INTO books (id, file_path, file_name, cover_path)
        VALUES (1, '/books/a.epub', 'a.epub', 'data/covers/1.jpg');
        """
    )
    conn.commit()
    conn.close()


def test_shard_covers_moves_file_and_updates_db() -> None:
    root = Path(__file__).resolve().parents[1]
    script = root / "scripts" / "shard-covers.py"

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        covers = base / "data" / "covers"
        covers.mkdir(parents=True)
        flat = covers / "1.jpg"
        flat.write_bytes(b"cover")
        db_path = base / "library.db"
        _init_db(db_path)

        result = subprocess.run(
            [
                sys.executable,
                str(script),
                "--database",
                str(db_path),
                "--covers-dir",
                str(covers),
                "--execute",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

        sharded = covers / "01" / "1.jpg"
        assert sharded.is_file()
        assert not flat.exists()

        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT cover_path FROM books WHERE id = 1").fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "data/covers/01/1.jpg"
