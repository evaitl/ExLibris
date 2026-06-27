from __future__ import annotations

import html
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import quote


@dataclass(frozen=True)
class BookRow:
    id: int
    file_path: str
    file_name: str
    format: str
    file_size: int
    file_mtime: float
    content_hash: str | None
    title: str | None
    sort_title: str | None
    authors: str | None
    publisher: str | None
    published_date: str | None
    isbn: str | None
    language: str | None
    description: str | None
    series: str | None
    series_index: float | None
    page_count: int | None
    cover_path: str | None
    tags: str | None
    first_seen_at: str
    last_scanned_at: str
    is_missing: int


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _default_database_path() -> Path:
    return project_root() / "data" / "library.db"


def _resolve_project_path(path: Path) -> Path:
    path = path.expanduser()
    if not path.is_absolute():
        path = project_root() / path
    return path.resolve()


def _load_yaml_config() -> dict:
    config = project_root() / "config.yaml"
    if not config.exists():
        return {}
    try:
        import yaml
    except ImportError:
        return {}
    data = yaml.safe_load(config.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def database_path() -> Path:
    env = os.environ.get("EXLIBRIS_DATABASE_PATH")
    if env:
        return Path(env).expanduser().resolve()
    data = _load_yaml_config()
    if data.get("database_path"):
        return _resolve_project_path(Path(data["database_path"]))
    db = _default_database_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    return db


def library_books_dirs() -> list[Path]:
    env = os.environ.get("EXLIBRIS_SCAN_PATHS")
    if env:
        return [
            Path(part.strip()).expanduser().resolve()
            for part in env.split(os.pathsep)
            if part.strip()
        ]
    data = _load_yaml_config()
    raw_paths = data.get("scan_paths")
    if isinstance(raw_paths, list) and raw_paths:
        return [_resolve_project_path(Path(str(path))) for path in raw_paths]
    return [Path("/media/books").resolve()]


def static_href() -> str:
    return os.environ.get("EXLIBRIS_STATIC_URL", "../static/style.css")


def static_asset(name: str) -> str:
    base = static_href().rsplit("/", 1)[0]
    return f"{base}/{name}"


def cgi_script(name: str) -> str:
    prefix = os.environ.get("EXLIBRIS_CGI_PREFIX", "")
    return f"{prefix}{name}"


def cover_href(book_id: int, *, version: str | None = None) -> str:
    url = f"{cgi_script('cover.py')}?id={book_id}"
    if version:
        url += f"&v={quote(version, safe='')}"
    return url


def download_href(book_id: int) -> str:
    return f"{cgi_script('download.py')}?id={book_id}"


def fetch_metadata_action() -> str:
    return cgi_script("fetch_metadata.py")


def restore_cover_action() -> str:
    return cgi_script("restore_cover.py")


def cover_cache_version(book: BookRow) -> str:
    """Cache-bust token for cover URLs; uses file mtime when the cover exists on disk."""
    if book.cover_path:
        path = project_root() / book.cover_path
        if path.is_file():
            return str(int(path.stat().st_mtime))
    return book.last_scanned_at.replace(":", "").replace("-", "").replace("+", "")


def allowed_book_file(file_path: str) -> Path | None:
    """Return resolved ebook path only if it lives under a configured books directory."""
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        return None
    for books_dir in library_books_dirs():
        try:
            path.relative_to(books_dir)
            return path
        except ValueError:
            continue
    return None


def connect() -> sqlite3.Connection:
    db = database_path()
    if not db.exists():
        raise FileNotFoundError(f"Library database not found: {db}")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def connect_rw() -> sqlite3.Connection:
    db = database_path()
    if not db.exists():
        raise FileNotFoundError(f"Library database not found: {db}")
    try:
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("ROLLBACK")
    except sqlite3.OperationalError as exc:
        if "readonly" in str(exc).lower() or "unable to open" in str(exc).lower():
            raise PermissionError(
                f"Cannot write to database at {db}. The web server user "
                f"(www-data) needs write permission on the database file and on "
                f"its parent directory {db.parent} (SQLite WAL files)."
            ) from exc
        raise
    return conn


def esc(value: object | None) -> str:
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def format_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.2f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


def format_published_date(value: str | None) -> str:
    """Format stored publication metadata for display (date only, no time)."""
    if not value or not value.strip():
        return ""
    raw = value.strip()

    if re.fullmatch(r"\d{4}", raw):
        return raw

    date_part = raw
    if "T" in raw:
        date_part = raw.split("T", 1)[0]
    elif " " in raw:
        date_part = raw.split(" ", 1)[0]

    if re.fullmatch(r"\d{4}-\d{2}", date_part):
        year_s, month_s = date_part.split("-", 1)
        try:
            return date(int(year_s), int(month_s), 1).strftime("%B %Y")
        except ValueError:
            return raw

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_part):
        try:
            parsed = date.fromisoformat(date_part)
            return f"{parsed.day} {parsed.strftime('%B %Y')}"
        except ValueError:
            return raw

    return raw


def row_to_book(row: sqlite3.Row) -> BookRow:
    return BookRow(
        id=row["id"],
        file_path=row["file_path"],
        file_name=row["file_name"],
        format=row["format"],
        file_size=row["file_size"],
        file_mtime=row["file_mtime"],
        content_hash=row["content_hash"],
        title=row["title"],
        sort_title=row["sort_title"],
        authors=row["authors"],
        publisher=row["publisher"],
        published_date=row["published_date"],
        isbn=row["isbn"],
        language=row["language"],
        description=row["description"],
        series=row["series"],
        series_index=row["series_index"],
        page_count=row["page_count"],
        cover_path=row["cover_path"] if "cover_path" in row.keys() else None,
        tags=row["tags"] if "tags" in row.keys() else None,
        first_seen_at=row["first_seen_at"],
        last_scanned_at=row["last_scanned_at"],
        is_missing=row["is_missing"],
    )


SORT_COLUMNS = {
    "title": "COALESCE(NULLIF(sort_title, ''), NULLIF(title, ''), file_name) COLLATE NOCASE",
    "author": "authors COLLATE NOCASE",
    "published": "published_date",
    "size": "file_size",
    "scanned": "last_scanned_at",
}

DEFAULT_SORT_DIR = {
    "title": "asc",
    "author": "asc",
    "published": "desc",
    "size": "desc",
    "scanned": "desc",
}

PAGE_SIZE_OPTIONS = (10, 25, 50, 100, 200)
DEFAULT_PAGE_SIZE = 100
PAGE_SIZE = DEFAULT_PAGE_SIZE


def normalize_page_size(page_size: int | str | None) -> int:
    try:
        size = int(page_size) if page_size is not None and str(page_size).strip() else DEFAULT_PAGE_SIZE
    except (TypeError, ValueError):
        return DEFAULT_PAGE_SIZE
    return size if size in PAGE_SIZE_OPTIONS else DEFAULT_PAGE_SIZE


def normalize_sort_dir(sort: str, sort_dir: str | None) -> str:
    if sort == "random":
        return "asc"
    if sort_dir in ("asc", "desc"):
        return sort_dir
    return DEFAULT_SORT_DIR.get(sort, "asc")


def sort_order_by(sort: str, sort_dir: str) -> str:
    if sort == "random":
        return "RANDOM()"
    direction = normalize_sort_dir(sort, sort_dir).upper()
    if sort == "published":
        return f"published_date IS NULL, published_date {direction}"
    column = SORT_COLUMNS.get(sort, SORT_COLUMNS["title"])
    return f"{column} {direction}"


@dataclass(frozen=True)
class FilterOptions:
    languages: list[str]


def load_filter_options(conn: sqlite3.Connection) -> FilterOptions:
    languages = [
        row[0]
        for row in conn.execute(
            """
            SELECT DISTINCT language FROM books
            WHERE is_missing = 0
              AND language IS NOT NULL
              AND TRIM(language) != ''
            ORDER BY language COLLATE NOCASE
            """
        ).fetchall()
    ]

    return FilterOptions(languages=languages)


def _search_words(text: str) -> list[str]:
    return [word for word in text.split() if word]


def _append_word_match(
    where: list[str],
    params: list[object],
    *,
    words: list[str],
    columns: list[str],
) -> None:
    """Require every word to match at least one column (case-insensitive substring)."""
    for word in words:
        pattern = f"%{word}%"
        if len(columns) == 1:
            where.append(f"{columns[0]} LIKE ? COLLATE NOCASE")
            params.append(pattern)
        else:
            or_clauses = " OR ".join(f"{col} LIKE ? COLLATE NOCASE" for col in columns)
            where.append(f"({or_clauses})")
            params.extend([pattern] * len(columns))


def has_search_filters(
    *,
    title: str = "",
    author: str = "",
    publisher: str = "",
    genre: str = "",
    language: str = "",
) -> bool:
    return bool(
        title.strip()
        or author.strip()
        or publisher.strip()
        or genre.strip()
        or language
    )


def _book_filter_clause(
    *,
    title: str,
    author: str,
    publisher: str,
    genre: str,
    language: str,
    sort: str,
    sort_dir: str = "asc",
) -> tuple[list[str], list[object], str]:
    order_by = sort_order_by(sort, sort_dir)
    params: list[object] = []
    where: list[str] = ["is_missing = 0"]

    if title.strip():
        _append_word_match(
            where,
            params,
            words=_search_words(title),
            columns=["title", "sort_title", "file_name"],
        )

    if author.strip():
        _append_word_match(
            where,
            params,
            words=_search_words(author),
            columns=["authors"],
        )

    if publisher.strip():
        _append_word_match(
            where,
            params,
            words=_search_words(publisher),
            columns=["publisher"],
        )

    if genre.strip():
        _append_word_match(
            where,
            params,
            words=_search_words(genre),
            columns=["tags"],
        )

    if language:
        where.append("language = ?")
        params.append(language)

    return where, params, order_by


def list_books(
    conn: sqlite3.Connection,
    *,
    title: str = "",
    author: str = "",
    publisher: str = "",
    genre: str = "",
    language: str = "",
    sort: str = "title",
    sort_dir: str = "asc",
    page: int = 1,
    page_size: int | str = DEFAULT_PAGE_SIZE,
) -> tuple[list[BookRow], int, int, int, FilterOptions]:
    """Return books, filtered_count, library_total, current_page, filter_options."""
    sort_dir = normalize_sort_dir(sort, sort_dir)
    page_size = normalize_page_size(page_size)
    library_total = int(
        conn.execute("SELECT COUNT(*) FROM books WHERE is_missing = 0").fetchone()[0]
    )
    options = load_filter_options(conn)

    where, params, order_by = _book_filter_clause(
        title=title,
        author=author,
        publisher=publisher,
        genre=genre,
        language=language,
        sort=sort,
        sort_dir=sort_dir,
    )
    where_sql = " AND ".join(where)

    filtered_count = int(
        conn.execute(f"SELECT COUNT(*) FROM books WHERE {where_sql}", params).fetchone()[0]
    )

    page = max(1, page)

    if sort == "random":
        limit = min(page_size, filtered_count) if filtered_count else 0
        if limit == 0:
            return [], filtered_count, library_total, page, options
        rows = conn.execute(
            f"""
            SELECT *
            FROM books
            WHERE {where_sql}
            ORDER BY RANDOM()
            LIMIT ?
            """,
            [*params, limit],
        ).fetchall()
        return [row_to_book(row) for row in rows], filtered_count, library_total, page, options

    max_page = max(1, (filtered_count + page_size - 1) // page_size)
    if page > max_page:
        page = max_page

    offset = (page - 1) * page_size
    rows = conn.execute(
        f"""
        SELECT *
        FROM books
        WHERE {where_sql}
        ORDER BY {order_by}, id
        LIMIT ? OFFSET ?
        """,
        [*params, page_size, offset],
    ).fetchall()

    return [row_to_book(row) for row in rows], filtered_count, library_total, page, options


def get_book(conn: sqlite3.Connection, book_id: int) -> BookRow | None:
    row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    return row_to_book(row) if row else None


def update_book_fields(conn: sqlite3.Connection, book_id: int, fields: dict[str, object]) -> None:
    if not fields:
        return
    columns = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [book_id]
    conn.execute(f"UPDATE books SET {columns} WHERE id = ?", values)
    conn.commit()
