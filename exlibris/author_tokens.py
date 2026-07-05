"""Author word tokens for fast prefix search without FTS5."""

from __future__ import annotations

import re
import sqlite3
import unicodedata

_TOKEN_SPLIT = re.compile(r"[^\w]+", re.UNICODE)


def tokenize_authors(authors: str | None) -> list[str]:
    """Split author text into lowercase ASCII tokens (diacritics removed)."""
    if not authors:
        return []
    normalized = unicodedata.normalize("NFKD", authors)
    without_marks = "".join(
        char for char in normalized if not unicodedata.combining(char)
    )
    tokens: list[str] = []
    seen: set[str] = set()
    for raw in _TOKEN_SPLIT.split(without_marks.lower()):
        token = raw.strip("_")
        if not token or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def _like_prefix(token: str) -> str:
    escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escaped}%"


def sync_author_tokens(
    conn: sqlite3.Connection,
    book_id: int,
    authors: str | None,
    *,
    commit: bool = True,
) -> None:
    try:
        conn.execute("DELETE FROM book_author_tokens WHERE book_id = ?", (book_id,))
        for token in tokenize_authors(authors):
            conn.execute(
                "INSERT OR IGNORE INTO book_author_tokens (book_id, token) VALUES (?, ?)",
                (book_id, token),
            )
        if commit:
            conn.commit()
    except sqlite3.OperationalError:
        return


def backfill_author_tokens(
    conn: sqlite3.Connection,
    *,
    batch_size: int = 2000,
) -> int:
    """Populate author tokens for all books. Returns rows processed."""
    conn.execute("DELETE FROM book_author_tokens")
    last_id = 0
    processed = 0
    while True:
        rows = conn.execute(
            """
            SELECT id, authors FROM books
            WHERE id > ?
            ORDER BY id
            LIMIT ?
            """,
            (last_id, batch_size),
        ).fetchall()
        if not rows:
            break
        for row in rows:
            book_id = int(row[0])
            authors = row[1]
            sync_author_tokens(conn, book_id, authors, commit=False)
            last_id = book_id
            processed += 1
        conn.commit()
    return processed


def author_tokens_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'book_author_tokens'
        """
    ).fetchone()
    return row is not None


def author_tokens_available(conn: sqlite3.Connection) -> bool:
    if not author_tokens_table_exists(conn):
        return False
    row = conn.execute("SELECT 1 FROM book_author_tokens LIMIT 1").fetchone()
    return row is not None

