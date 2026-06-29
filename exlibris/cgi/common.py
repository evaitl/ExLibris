from __future__ import annotations

import html
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import quote, urlencode

from exlibris.admins import is_admin_username
from exlibris.auth import (
    SESSION_COOKIE,
    create_session_token,
    parse_cookie_header,
    parse_session_token,
    session_secret,
)
from exlibris.cgi.search import build_fts_match, search_words


@dataclass(frozen=True)
class UserRow:
    id: int
    username: str


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


def login_action() -> str:
    return cgi_script("login.py")


def register_action() -> str:
    return cgi_script("register.py")


def logout_action() -> str:
    return cgi_script("logout.py")


def favorite_action() -> str:
    return cgi_script("favorite.py")


def edit_book_action() -> str:
    return cgi_script("edit_book.py")


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
    "title": "COALESCE(NULLIF(books.sort_title, ''), NULLIF(books.title, ''), books.file_name) COLLATE NOCASE",
    "author": "books.authors COLLATE NOCASE",
    "published": "books.published_date",
    "size": "books.file_size",
    "pages": "books.page_count",
    "scanned": "books.last_scanned_at",
}

DEFAULT_SORT_DIR = {
    "title": "asc",
    "author": "asc",
    "published": "desc",
    "size": "desc",
    "pages": "desc",
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


@dataclass(frozen=True)
class LibraryBrowseContext:
    """Library filters/sort carried on book detail links for prev/next navigation."""

    title: str = ""
    author: str = ""
    publisher: str = ""
    genre: str = ""
    language: str = ""
    sort: str = "title"
    sort_dir: str = ""
    page_size: int | str = ""
    page: int = 1
    favorites_only: bool = False

    def normalized(self) -> LibraryBrowseContext:
        return LibraryBrowseContext(
            title=self.title,
            author=self.author,
            publisher=self.publisher,
            genre=self.genre,
            language=self.language,
            sort=self.sort or "title",
            sort_dir=normalize_sort_dir(self.sort or "title", self.sort_dir or None),
            page_size=normalize_page_size(self.page_size),
            page=max(1, self.page),
            favorites_only=self.favorites_only,
        )

    def query_params(self, *, book_id: int | None = None) -> dict[str, str]:
        ctx = self.normalized()
        params: dict[str, str] = {}
        if book_id is not None:
            params["id"] = str(book_id)
        if ctx.title:
            params["title"] = ctx.title
        if ctx.author:
            params["author"] = ctx.author
        if ctx.publisher:
            params["publisher"] = ctx.publisher
        if ctx.genre:
            params["genre"] = ctx.genre
        if ctx.language:
            params["language"] = ctx.language
        if ctx.favorites_only:
            params["favorites"] = "1"
        if ctx.sort != "title":
            params["sort"] = ctx.sort
        if ctx.sort_dir != DEFAULT_SORT_DIR.get(ctx.sort, "asc"):
            params["sort_dir"] = ctx.sort_dir
        if ctx.page_size != DEFAULT_PAGE_SIZE:
            params["page_size"] = str(ctx.page_size)
        if ctx.page > 1:
            params["page"] = str(ctx.page)
        return params


def parse_library_browse_context(
    *,
    title: str = "",
    author: str = "",
    publisher: str = "",
    genre: str = "",
    language: str = "",
    sort: str = "title",
    sort_dir: str = "",
    page_size: str | int = "",
    page: str | int = "1",
    favorites_only: bool = False,
) -> LibraryBrowseContext:
    raw_page = str(page).strip() if page is not None else "1"
    parsed_page = int(raw_page) if raw_page.isdigit() else 1
    return LibraryBrowseContext(
        title=title or "",
        author=author or "",
        publisher=publisher or "",
        genre=genre or "",
        language=language or "",
        sort=sort or "title",
        sort_dir=sort_dir or "",
        page_size=page_size or DEFAULT_PAGE_SIZE,
        page=parsed_page,
        favorites_only=favorites_only,
    )


def book_detail_href(book_id: int, ctx: LibraryBrowseContext) -> str:
    query = urlencode(ctx.query_params(book_id=book_id))
    return f"{cgi_script('book.py')}?{query}"


def library_index_href(ctx: LibraryBrowseContext) -> str:
    params = ctx.normalized().query_params()
    query = urlencode(params)
    return f"{cgi_script('index.py')}?{query}" if query else cgi_script("index.py")


def sort_order_by(sort: str, sort_dir: str) -> str:
    if sort == "random":
        return "RANDOM()"
    direction = normalize_sort_dir(sort, sort_dir).upper()
    if sort == "published":
        return f"books.published_date IS NULL, books.published_date {direction}"
    if sort == "pages":
        return f"books.page_count IS NULL, books.page_count {direction}"
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
    return search_words(text)


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
    favorites_only: bool = False,
) -> bool:
    return bool(
        title.strip()
        or author.strip()
        or publisher.strip()
        or genre.strip()
        or language
        or favorites_only
    )


def get_current_user(conn: sqlite3.Connection) -> UserRow | None:
    cookie_header = os.environ.get("HTTP_COOKIE", "")
    token = parse_cookie_header(cookie_header, SESSION_COOKIE)
    if not token:
        return None
    secret = session_secret(fallback_seed=str(database_path()))
    parsed = parse_session_token(token, secret=secret)
    if parsed is None:
        return None
    user_id, username = parsed
    row = conn.execute(
        "SELECT id, username FROM users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if row is None or row["username"] != username:
        return None
    return UserRow(id=int(row["id"]), username=str(row["username"]))


def authenticate_user(
    conn: sqlite3.Connection, *, username: str, password: str
) -> UserRow | None:
    """Verify credentials against the stored scrypt hash (plain passwords are never kept)."""
    row = conn.execute(
        "SELECT id, username, password_hash FROM users WHERE username = ? COLLATE NOCASE",
        (username.strip(),),
    ).fetchone()
    if row is None:
        return None
    from exlibris.auth import verify_password

    if not verify_password(password, row["password_hash"]):
        return None
    return UserRow(id=int(row["id"]), username=str(row["username"]))


def create_session_for_user(user: UserRow) -> str:
    secret = session_secret(fallback_seed=str(database_path()))
    return create_session_token(user_id=user.id, username=user.username, secret=secret)


def is_favorite(conn: sqlite3.Connection, *, user_id: int, book_id: int) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM user_favorites
        WHERE user_id = ? AND book_id = ?
        """,
        (user_id, book_id),
    ).fetchone()
    return row is not None


def set_favorite(
    conn: sqlite3.Connection,
    *,
    user_id: int,
    book_id: int,
    favorite: bool,
) -> None:
    if favorite:
        conn.execute(
            """
            INSERT OR IGNORE INTO user_favorites (user_id, book_id)
            VALUES (?, ?)
            """,
            (user_id, book_id),
        )
    else:
        conn.execute(
            "DELETE FROM user_favorites WHERE user_id = ? AND book_id = ?",
            (user_id, book_id),
        )
    conn.commit()


def book_detail_context(
    conn: sqlite3.Connection, book_id: int
) -> tuple[UserRow | None, bool]:
    user = get_current_user(conn)
    favorite = (
        is_favorite(conn, user_id=user.id, book_id=book_id) if user is not None else False
    )
    return user, favorite


def is_admin(user: UserRow | None) -> bool:
    return is_admin_username(user.username if user is not None else None)


def _book_filter_clause(
    *,
    title: str,
    author: str,
    publisher: str,
    genre: str,
    language: str,
    sort: str,
    sort_dir: str = "asc",
    favorites_only: bool = False,
    user_id: int | None = None,
) -> tuple[str | None, list[str], list[object], str]:
    order_by = sort_order_by(sort, sort_dir)
    params: list[object] = []
    where: list[str] = ["books.is_missing = 0"]
    fts_match = build_fts_match(
        title=title,
        author=author,
        publisher=publisher,
        genre=genre,
    )

    if favorites_only and user_id is not None:
        where.append(
            "books.id IN (SELECT book_id FROM user_favorites WHERE user_id = ?)"
        )
        params.append(user_id)

    if fts_match is None:
        if title.strip():
            _append_word_match(
                where,
                params,
                words=_search_words(title),
                columns=["books.title", "books.sort_title", "books.file_name"],
            )
        if author.strip():
            _append_word_match(
                where,
                params,
                words=_search_words(author),
                columns=["books.authors"],
            )
        if publisher.strip():
            _append_word_match(
                where,
                params,
                words=_search_words(publisher),
                columns=["books.publisher"],
            )
        if genre.strip():
            _append_word_match(
                where,
                params,
                words=_search_words(genre),
                columns=["books.tags"],
            )

    if language:
        where.append("books.language = ?")
        params.append(language)

    return fts_match, where, params, order_by


def _filtered_count_sql(
    fts_match: str | None, where: list[str], params: list[object]
) -> tuple[str, list[object]]:
    where_sql = " AND ".join(where)
    if fts_match:
        sql = (
            "SELECT COUNT(*) FROM books "
            "INNER JOIN books_fts ON books_fts.rowid = books.id "
            f"WHERE books_fts MATCH ? AND {where_sql}"
        )
        return sql, [fts_match, *params]
    return f"SELECT COUNT(*) FROM books WHERE {where_sql}", list(params)


def _select_books_sql(
    fts_match: str | None,
    where: list[str],
    params: list[object],
    order_by: str,
    *,
    limit: int | None = None,
    offset: int | None = None,
) -> tuple[str, list[object]]:
    where_sql = " AND ".join(where)
    if fts_match:
        sql = (
            "SELECT books.* FROM books "
            "INNER JOIN books_fts ON books_fts.rowid = books.id "
            f"WHERE books_fts MATCH ? AND {where_sql} "
            f"ORDER BY {order_by}, books.id"
        )
        query_params: list[object] = [fts_match, *params]
    else:
        sql = (
            f"SELECT books.* FROM books WHERE {where_sql} "
            f"ORDER BY {order_by}, books.id"
        )
        query_params = list(params)
    if limit is not None:
        sql += " LIMIT ?"
        query_params.append(limit)
    if offset is not None:
        sql += " OFFSET ?"
        query_params.append(offset)
    return sql, query_params


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
    favorites_only: bool = False,
    user_id: int | None = None,
) -> tuple[list[BookRow], int, int, int, FilterOptions]:
    """Return books, filtered_count, library_total, current_page, filter_options."""
    sort_dir = normalize_sort_dir(sort, sort_dir)
    page_size = normalize_page_size(page_size)
    library_total = int(
        conn.execute("SELECT COUNT(*) FROM books WHERE is_missing = 0").fetchone()[0]
    )
    options = load_filter_options(conn)

    fts_match, where, params, order_by = _book_filter_clause(
        title=title,
        author=author,
        publisher=publisher,
        genre=genre,
        language=language,
        sort=sort,
        sort_dir=sort_dir,
        favorites_only=favorites_only,
        user_id=user_id,
    )

    count_sql, count_params = _filtered_count_sql(fts_match, where, params)
    filtered_count = int(conn.execute(count_sql, count_params).fetchone()[0])

    page = max(1, page)

    if sort == "random":
        limit = min(page_size, filtered_count) if filtered_count else 0
        if limit == 0:
            return [], filtered_count, library_total, page, options
        if fts_match:
            random_sql = (
                "SELECT books.* FROM books "
                "INNER JOIN books_fts ON books_fts.rowid = books.id "
                f"WHERE books_fts MATCH ? AND {' AND '.join(where)} "
                "ORDER BY RANDOM() LIMIT ?"
            )
            random_params: list[object] = [fts_match, *params, limit]
        else:
            random_sql = (
                f"SELECT books.* FROM books WHERE {' AND '.join(where)} "
                "ORDER BY RANDOM() LIMIT ?"
            )
            random_params = [*params, limit]
        rows = conn.execute(random_sql, random_params).fetchall()
        return [row_to_book(row) for row in rows], filtered_count, library_total, page, options

    max_page = max(1, (filtered_count + page_size - 1) // page_size)
    if page > max_page:
        page = max_page

    offset = (page - 1) * page_size
    select_sql, select_params = _select_books_sql(
        fts_match,
        where,
        params,
        order_by,
        limit=page_size,
        offset=offset,
    )
    rows = conn.execute(select_sql, select_params).fetchall()

    return [row_to_book(row) for row in rows], filtered_count, library_total, page, options


def neighbor_book_ids(
    conn: sqlite3.Connection,
    book_id: int,
    ctx: LibraryBrowseContext,
    *,
    user_id: int | None = None,
) -> tuple[int | None, int | None]:
    """Previous and next book ids in the current filtered/sorted library view."""
    ctx = ctx.normalized()
    if ctx.sort == "random":
        return None, None

    fts_match, where, params, order_by = _book_filter_clause(
        title=ctx.title,
        author=ctx.author,
        publisher=ctx.publisher,
        genre=ctx.genre,
        language=ctx.language,
        sort=ctx.sort,
        sort_dir=ctx.sort_dir,
        favorites_only=ctx.favorites_only,
        user_id=user_id,
    )
    where_sql = " AND ".join(where)
    if fts_match:
        from_sql = (
            "FROM books INNER JOIN books_fts ON books_fts.rowid = books.id "
            f"WHERE books_fts MATCH ? AND {where_sql}"
        )
        query_params: list[object] = [fts_match, *params, book_id]
    else:
        from_sql = f"FROM books WHERE {where_sql}"
        query_params = [*params, book_id]

    sql = f"""
        WITH ordered AS (
            SELECT books.id AS id,
                   ROW_NUMBER() OVER (ORDER BY {order_by}, books.id) AS pos
            {from_sql}
        )
        SELECT prev.id, next.id
        FROM ordered cur
        LEFT JOIN ordered prev ON prev.pos = cur.pos - 1
        LEFT JOIN ordered next ON next.pos = cur.pos + 1
        WHERE cur.id = ?
    """
    row = conn.execute(sql, query_params).fetchone()
    if row is None:
        return None, None
    prev_id = int(row[0]) if row[0] is not None else None
    next_id = int(row[1]) if row[1] is not None else None
    return prev_id, next_id


def get_book(
    conn: sqlite3.Connection,
    book_id: int,
    *,
    include_missing: bool = False,
) -> BookRow | None:
    if include_missing:
        row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM books WHERE id = ? AND is_missing = 0",
            (book_id,),
        ).fetchone()
    return row_to_book(row) if row else None


def update_book_fields(conn: sqlite3.Connection, book_id: int, fields: dict[str, object]) -> None:
    if not fields:
        return
    columns = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [book_id]
    conn.execute(f"UPDATE books SET {columns} WHERE id = ?", values)
    conn.commit()


class EditBookError(Exception):
    pass


def book_edit_fields(
    *,
    title: str | None,
    authors: str | None,
    genre: str | None,
) -> dict[str, object]:
    """Validate and build DB updates for a manual metadata edit."""
    cleaned_title = (title or "").strip()
    cleaned_authors = (authors or "").strip()
    cleaned_genre = (genre or "").strip()
    if not cleaned_title:
        raise EditBookError("Title cannot be empty.")
    return {
        "title": cleaned_title,
        "authors": cleaned_authors or None,
        "sort_title": cleaned_title,
        "tags": cleaned_genre or None,
    }
