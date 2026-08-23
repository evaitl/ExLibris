"""Tests for closed-set genre parsing and classification."""

from __future__ import annotations

import sqlite3
import tempfile
import zipfile
from pathlib import Path

import pytest

from exlibris.cgi.common import (
    EditBookError,
    book_edit_fields,
    get_book,
    list_books,
)
from exlibris.classify import classify_text, sample_epub_text
from exlibris.classify_job import classify_library
from exlibris.epub_validate import iter_spine_xhtml
from exlibris.genres import (
    format_genre_labels,
    genre_contains_erotica,
    parse_genre_labels,
)
from exlibris.library_cache import refresh_library_stats


def _write_epub(path: Path, chapters: list[str]) -> None:
    manifest_items = []
    spine_items = []
    for index, _body in enumerate(chapters, start=1):
        manifest_items.append(
            f'    <item id="c{index}" href="c{index}.xhtml" '
            f'media-type="application/xhtml+xml"/>'
        )
        spine_items.append(f'    <itemref idref="c{index}"/>')
    container = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""
    opf = f"""<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Sample</dc:title>
  </metadata>
  <manifest>
{chr(10).join(manifest_items)}
  </manifest>
  <spine>
{chr(10).join(spine_items)}
  </spine>
</package>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", opf)
        for index, body in enumerate(chapters, start=1):
            archive.writestr(
                f"OEBPS/c{index}.xhtml",
                '<?xml version="1.0"?>\n'
                '<html xmlns="http://www.w3.org/1999/xhtml">'
                f"<head><title>C{index}</title></head>"
                f"<body><p>{body}</p></body></html>",
            )


def _fantasy_prose() -> str:
    return (
        'The wizard said, "The dragon waits." He said the enchanted sword '
        "and the mage's spell would end the quest. Elves and dwarves joined "
        "the kingdom. Magic, wizard, dragon, spell, quest, mage. " * 8
    )


def _romance_prose() -> str:
    return (
        'She said, "I love you." He kissed her. Darling, soulmate, wedding, '
        "falling in love, her heart raced, romance, kissed, beloved. " * 8
    )


def _mixed_fantasy_romance() -> str:
    return (
        'He said, "Come with me." The dragon and the wizard. She kissed him. '
        "Enchanted spell, soulmate, romance, magic, quest, darling, wedding, "
        "elves, heart raced, mage. " * 8
    )


def _erotica_romance() -> str:
    return (
        'She said, "Yes." He kissed her. Romance, darling, soulmate, wedding. '
        "Then a hard cock, wet pussy, he fucked her, orgasm, thrust into her. "
        "Fucking, cock, pussy, orgasm. " * 6
    )


def _medical_nonfiction() -> str:
    return (
        "According to this book, figure 1 shows the anatomy of the patient. "
        "The physician records a diagnosis after surgery. Clinical trial "
        "pathology and medical anatomy, penis, vagina, uterus as organs. "
        "In this book the author explains the experiment. References. " * 8
    )


def test_parse_and_format_genre_labels() -> None:
    assert parse_genre_labels("Fantasy, Romance") == ["Fantasy", "Romance"]
    assert parse_genre_labels("fantasy, ROMANCE, fantasy") == ["Fantasy", "Romance"]
    assert format_genre_labels(["Fantasy", "Romance", "Adventure", "Horror"]) == (
        "Fantasy, Romance, Adventure"
    )
    with pytest.raises(ValueError, match="Unknown genre"):
        parse_genre_labels("Narnia")


def test_book_edit_fields_writes_genre_not_tags() -> None:
    fields = book_edit_fields(
        title="Title",
        authors="Author",
        genre="fantasy, romance",
    )
    assert fields["genre"] == "Fantasy, Romance"
    assert fields["genre_source"] == "manual"
    assert "tags" not in fields
    with pytest.raises(EditBookError, match="Unknown genre"):
        book_edit_fields(title="Title", authors="", genre="Nope")


def test_classify_clear_fantasy_winner() -> None:
    result = classify_text(_fantasy_prose(), title="The Dragon Quest")
    assert result.labels == ["Fantasy"]


def test_classify_close_fantasy_and_romance() -> None:
    result = classify_text(_mixed_fantasy_romance(), title="A Kiss and a Dragon")
    assert "Fantasy" in result.labels
    assert "Romance" in result.labels
    assert len(result.labels) >= 2


def test_classify_erotica_keeps_romance() -> None:
    result = classify_text(_erotica_romance(), title="A Passionate Romance")
    assert "Romance" in result.labels
    assert "Erotica" in result.labels
    assert result.labels[-1] == "Erotica"


def test_classify_nonfiction_anatomy_is_not_erotica() -> None:
    result = classify_text(_medical_nonfiction(), title="Clinical Anatomy")
    assert "Erotica" not in result.labels
    assert result.labels
    assert "Fiction" not in result.labels or result.labels == ["Non-fiction"] or any(
        label in {"Medicine", "Science", "Non-fiction"} for label in result.labels
    )


def test_classify_empty_sample_is_empty() -> None:
    result = classify_text("   ")
    assert result.labels == []
    assert result.genre is None


def test_sample_epub_text_reads_spine(tmp_path: Path) -> None:
    path = tmp_path / "book.epub"
    _write_epub(path, ["Hello wizard dragon", "Later chapter magic"])
    chunks = list(iter_spine_xhtml(path))
    assert len(chunks) == 2
    sample = sample_epub_text(path)
    assert "wizard" in sample
    assert "magic" in sample


def _genre_db(tmp_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(tmp_path / "library.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT NOT NULL UNIQUE,
            file_name TEXT NOT NULL,
            format TEXT NOT NULL DEFAULT 'epub',
            file_size INTEGER NOT NULL DEFAULT 0,
            file_mtime REAL NOT NULL DEFAULT 0,
            content_hash TEXT,
            title TEXT,
            sort_title TEXT,
            authors TEXT,
            publisher TEXT,
            published_date TEXT,
            isbn TEXT,
            language TEXT,
            description TEXT,
            series TEXT,
            series_index REAL,
            page_count INTEGER,
            cover_path TEXT,
            tags TEXT,
            genre TEXT,
            genre_source TEXT,
            classified_content_hash TEXT,
            first_seen_at TEXT NOT NULL DEFAULT 'now',
            last_scanned_at TEXT NOT NULL DEFAULT 'now',
            is_missing INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE library_stats (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            library_total INTEGER NOT NULL,
            languages TEXT NOT NULL,
            refreshed_at TEXT NOT NULL
        );
        """
    )
    return conn


def _insert(
    conn: sqlite3.Connection,
    *,
    title: str,
    genre: str | None = None,
    file_path: str | None = None,
) -> int:
    name = f"{title}.epub"
    cur = conn.execute(
        """
        INSERT INTO books (file_path, file_name, title, sort_title, genre, content_hash)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (file_path or f"/books/{name}", name, title, title, genre, "hash"),
    )
    conn.commit()
    return int(cur.lastrowid)


def test_list_books_hides_erotica_and_matches_tokens(tmp_path: Path) -> None:
    conn = _genre_db(tmp_path)
    _insert(conn, title="Dragon Tale", genre="Fantasy, Adventure")
    _insert(conn, title="Steamy", genre="Romance, Erotica")
    _insert(conn, title="History Book", genre="Historical Fiction")
    refresh_library_stats(conn)

    public, count, total, *_ = list_books(conn, hide_erotica=True)
    titles = {book.title for book in public}
    assert titles == {"Dragon Tale", "History Book"}
    assert total == 2
    assert count == 2

    admin, admin_count, admin_total, *_ = list_books(conn, hide_erotica=False)
    assert {book.title for book in admin} == {
        "Dragon Tale",
        "Steamy",
        "History Book",
    }
    assert admin_total == 3

    fantasy, *_ = list_books(conn, genre="Fantasy", hide_erotica=True)
    assert [book.title for book in fantasy] == ["Dragon Tale"]

    fiction, *_ = list_books(conn, genre="Fiction", hide_erotica=True)
    assert fiction == []

    hidden, *_ = list_books(conn, genre="Erotica", hide_erotica=True)
    assert hidden == []


def test_get_book_hides_erotica(tmp_path: Path) -> None:
    conn = _genre_db(tmp_path)
    public_id = _insert(conn, title="Open", genre="Mystery")
    hidden_id = _insert(conn, title="Hidden", genre="Fantasy, Erotica")
    assert get_book(conn, public_id, hide_erotica=True) is not None
    assert get_book(conn, hidden_id, hide_erotica=True) is None
    assert get_book(conn, hidden_id, hide_erotica=False) is not None


def test_genre_contains_erotica() -> None:
    assert genre_contains_erotica("Fantasy, Erotica")
    assert not genre_contains_erotica("Fantasy")


def test_classify_library_skips_manual(tmp_path: Path) -> None:
    epub = tmp_path / "dragon.epub"
    _write_epub(epub, [_fantasy_prose()])
    conn = _genre_db(tmp_path)
    book_id = _insert(
        conn,
        title="Dragon",
        genre="Mystery",
        file_path=str(epub),
    )
    conn.execute(
        "UPDATE books SET genre_source = 'manual', content_hash = 'abc' WHERE id = ?",
        (book_id,),
    )
    conn.commit()
    stats = classify_library(conn, execute=True)
    assert stats.skipped >= 1
    row = conn.execute("SELECT genre FROM books WHERE id = ?", (book_id,)).fetchone()
    assert row["genre"] == "Mystery"

    stats = classify_library(conn, execute=True, overwrite_manual=True)
    assert stats.classified == 1
    row = conn.execute("SELECT genre, genre_source FROM books WHERE id = ?", (book_id,)).fetchone()
    assert "Fantasy" in row["genre"]
    assert row["genre_source"] == "auto"
