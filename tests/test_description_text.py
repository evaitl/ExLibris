"""Tests for plain-text book descriptions."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from exlibris.cleanup import strip_book_descriptions
from exlibris.description_text import (
    description_needs_plaintext,
    plain_text_description,
)


def test_plain_text_description_strips_tags() -> None:
    assert (
        plain_text_description("<p>Hello <b>world</b>.</p>")
        == "Hello world."
    )


def test_plain_text_description_unescapes_entities() -> None:
    assert plain_text_description("Tom &amp; Jerry") == "Tom & Jerry"
    assert plain_text_description("it&#39;s fine") == "it's fine"
    assert plain_text_description("&quot;quoted&quot;") == '"quoted"'


def test_plain_text_description_unescapes_double_encoded_entities() -> None:
    assert plain_text_description("&amp;amp;") == "&"


def test_plain_text_description_block_tags_add_space() -> None:
    assert (
        plain_text_description("<p>First</p><p>Second</p>")
        == "First Second"
    )


def test_plain_text_description_skips_script_content() -> None:
    assert (
        plain_text_description("<p>Safe</p><script>alert(1)</script>")
        == "Safe"
    )


def test_plain_text_description_empty_becomes_none() -> None:
    assert plain_text_description("<p></p>") is None
    assert plain_text_description("   ") is None
    assert plain_text_description(None) is None


def test_description_needs_plaintext() -> None:
    assert description_needs_plaintext("Plain text") is False
    assert description_needs_plaintext("<p>HTML</p>") is True
    assert description_needs_plaintext("A &amp; B") is True


def _init_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE books (
            id INTEGER PRIMARY KEY,
            file_name TEXT NOT NULL,
            description TEXT
        );
        """
    )
    return conn


def test_strip_book_descriptions_updates_rows_incrementally() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = _init_db(Path(tmp) / "library.db")
        conn.execute(
            "INSERT INTO books (id, file_name, description) VALUES (1, 'a.epub', ?)",
            ("<p>One</p>",),
        )
        conn.execute(
            "INSERT INTO books (id, file_name, description) VALUES (2, 'b.epub', ?)",
            ("Plain",),
        )
        conn.execute(
            "INSERT INTO books (id, file_name, description) VALUES (3, 'c.epub', ?)",
            ("&amp; more",),
        )
        conn.commit()

        updated, errors = strip_book_descriptions(conn, execute=True)
        assert errors == []
        assert updated == 2

        rows = {
            int(row["id"]): row["description"]
            for row in conn.execute("SELECT id, description FROM books ORDER BY id")
        }
        assert rows[1] == "One"
        assert rows[2] == "Plain"
        assert rows[3] == "& more"
        conn.close()


def test_strip_book_descriptions_dry_run_leaves_database() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = _init_db(Path(tmp) / "library.db")
        conn.execute(
            "INSERT INTO books (id, file_name, description) VALUES (1, 'a.epub', ?)",
            ("<p>Keep</p>",),
        )
        conn.commit()

        updated, _errors = strip_book_descriptions(conn, execute=False)
        assert updated == 1
        row = conn.execute(
            "SELECT description FROM books WHERE id = 1"
        ).fetchone()
        assert row["description"] == "<p>Keep</p>"
        conn.close()
