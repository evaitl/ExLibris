"""Cached library totals and filter options (refreshed on scan and book delete)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone


def _load_languages(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT language FROM books
        WHERE is_missing = 0
          AND language IS NOT NULL
          AND TRIM(language) != ''
        ORDER BY language COLLATE NOCASE
        """
    ).fetchall()
    return [str(row[0]) for row in rows]


def refresh_library_stats(conn: sqlite3.Connection) -> None:
    """Recompute and store library_total and language filter options."""
    try:
        library_total = int(
            conn.execute(
                "SELECT COUNT(*) FROM books WHERE is_missing = 0"
            ).fetchone()[0]
        )
        languages = _load_languages(conn)
        refreshed_at = datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO library_stats (id, library_total, languages, refreshed_at)
            VALUES (1, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                library_total = excluded.library_total,
                languages = excluded.languages,
                refreshed_at = excluded.refreshed_at
            """,
            (library_total, json.dumps(languages), refreshed_at),
        )
        conn.commit()
    except sqlite3.OperationalError:
        return


def _stats_row(conn: sqlite3.Connection) -> sqlite3.Row | None:
    try:
        return conn.execute(
            "SELECT library_total, languages FROM library_stats WHERE id = 1"
        ).fetchone()
    except sqlite3.OperationalError:
        return None


def cached_library_total(conn: sqlite3.Connection) -> int:
    row = _stats_row(conn)
    if row is None:
        refresh_library_stats(conn)
        row = _stats_row(conn)
    if row is None:
        return int(
            conn.execute(
                "SELECT COUNT(*) FROM books WHERE is_missing = 0"
            ).fetchone()[0]
        )
    return int(row["library_total"])


def cached_languages(conn: sqlite3.Connection) -> list[str]:
    row = _stats_row(conn)
    if row is None:
        refresh_library_stats(conn)
        row = _stats_row(conn)
    if row is None:
        return _load_languages(conn)
    try:
        languages = json.loads(str(row["languages"]))
    except (json.JSONDecodeError, TypeError):
        return _load_languages(conn)
    if not isinstance(languages, list):
        return _load_languages(conn)
    return [str(lang) for lang in languages]
