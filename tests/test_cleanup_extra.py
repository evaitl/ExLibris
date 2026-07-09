import tempfile
from pathlib import Path

from exlibris.book_paths import prune_empty_directories
from exlibris.cleanup import backfill_content_hashes


def test_backfill_content_hashes() -> None:
    import sqlite3

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        epub = root / "book.epub"
        epub.write_bytes(b"content-for-hash")

        conn = sqlite3.connect(root / "library.db")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE books (
                id INTEGER PRIMARY KEY,
                file_path TEXT NOT NULL,
                file_name TEXT NOT NULL,
                content_hash TEXT,
                is_missing INTEGER NOT NULL DEFAULT 0
            );
            CREATE UNIQUE INDEX idx_books_content_hash ON books (content_hash)
                WHERE content_hash IS NOT NULL;
            """
        )
        conn.execute(
            "INSERT INTO books (file_path, file_name) VALUES (?, ?)",
            (str(epub), epub.name),
        )
        conn.commit()

        updated, errors = backfill_content_hashes(conn, execute=True)
        assert errors == []
        assert updated == 1
        row = conn.execute("SELECT content_hash FROM books WHERE id = 1").fetchone()
        assert row["content_hash"] is not None

        conn.execute("UPDATE books SET content_hash = NULL WHERE id = 1")
        conn.commit()
        seen: list[tuple[int, int, str]] = []
        updated, errors = backfill_content_hashes(
            conn,
            execute=True,
            on_progress=lambda current, total, item: seen.append(
                (current, total, item)
            ),
        )
        assert errors == []
        assert updated == 1
        assert seen == [(1, 1, epub.name)]
        conn.close()


def test_prune_empty_directories() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        empty = root / "nested" / "empty"
        empty.mkdir(parents=True)
        (root / "nested" / "keep.epub").write_bytes(b"x")

        removed = prune_empty_directories([root], execute=True)
        assert removed == 1
        assert not empty.exists()
        assert (root / "nested").is_dir()
