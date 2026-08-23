"""Batch classify indexed EPUBs into books.genre (CLI job)."""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from exlibris.classify import DEFAULT_ADULT_THRESHOLD, classify_epub
from exlibris.library_cache import refresh_library_stats
from exlibris.sqlite_retry import configure_sqlite_connection, run_write_with_retry


@dataclass
class ClassifyStats:
    examined: int = 0
    classified: int = 0
    skipped: int = 0
    unchanged: int = 0
    failed: int = 0
    empty: int = 0
    histogram: Counter[str] = field(default_factory=Counter)
    samples: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _path_matches(file_path: str, filters: list[Path] | None) -> bool:
    if not filters:
        return True
    resolved = Path(file_path).expanduser()
    try:
        resolved = resolved.resolve()
    except OSError:
        pass
    file_s = str(resolved)
    for filter_path in filters:
        candidate = filter_path.expanduser()
        try:
            candidate = candidate.resolve()
        except OSError:
            pass
        cand_s = str(candidate)
        if file_s == cand_s or file_s.startswith(cand_s.rstrip("/") + "/"):
            return True
    return False


def _should_skip(
    *,
    genre: str | None,
    genre_source: str | None,
    content_hash: str | None,
    classified_hash: str | None,
    overwrite: bool,
    overwrite_manual: bool,
) -> str | None:
    source = (genre_source or "").strip().lower()
    if source == "manual" and not overwrite_manual:
        return "manual"
    if genre and not overwrite and source != "manual":
        return "filled"
    if genre and source == "manual" and not overwrite_manual:
        return "manual"
    if (
        overwrite
        and genre
        and source != "manual"
        and content_hash
        and classified_hash == content_hash
    ):
        return "unchanged"
    return None


def classify_library(
    conn: sqlite3.Connection,
    *,
    execute: bool = True,
    overwrite: bool = False,
    overwrite_manual: bool = False,
    adult_threshold: float = DEFAULT_ADULT_THRESHOLD,
    path_filters: list[Path] | None = None,
    limit: int | None = None,
    on_progress=None,
) -> ClassifyStats:
    stats = ClassifyStats()
    rows = conn.execute(
        """
        SELECT id, file_path, title, description, language, content_hash,
               genre, genre_source, classified_content_hash, is_missing
        FROM books
        WHERE is_missing = 0
        ORDER BY id
        """
    ).fetchall()

    processed = 0
    for row in rows:
        if limit is not None and processed >= limit:
            break
        file_path = str(row["file_path"])
        if not _path_matches(file_path, path_filters):
            continue
        processed += 1
        stats.examined += 1
        skip = _should_skip(
            genre=row["genre"],
            genre_source=row["genre_source"],
            content_hash=row["content_hash"],
            classified_hash=row["classified_content_hash"],
            overwrite=overwrite,
            overwrite_manual=overwrite_manual,
        )
        if skip == "unchanged":
            stats.unchanged += 1
            stats.skipped += 1
            continue
        if skip:
            stats.skipped += 1
            continue

        path = Path(file_path)
        title = row["title"]
        try:
            result = classify_epub(
                path,
                title=title,
                description=row["description"],
                language=row["language"],
                adult_threshold=adult_threshold,
            )
        except OSError as exc:
            stats.failed += 1
            stats.errors.append(f"{file_path}: {exc}")
            continue

        genre = result.genre
        display = title or path.name
        sample_line = (
            f"{display}: {genre or '(none)'} "
            f"(adult={result.adult_density:.1f}/1000)"
        )
        if len(stats.samples) < 30:
            stats.samples.append(sample_line)
        if genre:
            for label in result.labels:
                stats.histogram[label] += 1
        else:
            stats.empty += 1

        if on_progress is not None:
            on_progress(sample_line)

        if not execute:
            stats.classified += 1
            continue

        book_id = int(row["id"])
        content_hash = row["content_hash"]

        def writer(
            conn: sqlite3.Connection = conn,
            book_id: int = book_id,
            genre: str | None = genre,
            content_hash: str | None = content_hash,
        ) -> None:
            conn.execute(
                """
                UPDATE books
                SET genre = ?, genre_source = 'auto', classified_content_hash = ?
                WHERE id = ?
                """,
                (genre, content_hash, book_id),
            )

        try:
            run_write_with_retry(conn, writer)
        except sqlite3.Error as exc:
            stats.failed += 1
            stats.errors.append(f"{file_path}: {exc}")
            continue
        stats.classified += 1

    if execute and stats.classified:
        refresh_library_stats(conn)
        conn.commit()
    return stats


def open_library_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    configure_sqlite_connection(conn)
    return conn
