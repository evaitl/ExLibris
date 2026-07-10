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
    ADMIN_MODE_COOKIE,
    SESSION_COOKIE,
    create_session_token,
    parse_cookie_header,
    parse_session_token,
    session_secret,
)
from exlibris.config import (
    load_settings,
    resolve_database_path,
    resolve_scan_path,
)
from exlibris.sqlite_retry import configure_sqlite_connection
from exlibris.author_tokens import author_tokens_available, sync_author_tokens
from exlibris.cgi.search import (
    append_author_token_filters,
    build_fts_match,
    fts_field_match,
    search_words,
)
from exlibris.cover_paths import (
    cover_public_segment,
    iter_cover_files,
    parse_book_id_from_cover,
    remove_cover_files,
)
from exlibris.library_cache import cached_languages, cached_library_total, refresh_library_stats


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


def database_path() -> Path:
    settings = load_settings()
    return resolve_database_path(settings.database_path)


def library_books_dirs() -> list[Path]:
    settings = load_settings()
    return [resolve_scan_path(path) for path in settings.scan_paths]


def static_href() -> str:
    return os.environ.get("EXLIBRIS_STATIC_URL", "../static/style.css")


def static_asset(name: str) -> str:
    base = static_href().rsplit("/", 1)[0]
    return f"{base}/{name}"


def cgi_script(name: str) -> str:
    prefix = os.environ.get("EXLIBRIS_CGI_PREFIX", "")
    return f"{prefix}{name}"


def covers_static_prefix() -> str:
    env = os.environ.get("EXLIBRIS_COVERS_URL")
    if env:
        return env.rstrip("/")
    static = static_href()
    if "/static/" in static:
        return f"{static.split('/static/', 1)[0]}/covers"
    if static.endswith("/static/style.css"):
        return static.replace("/static/style.css", "/covers")
    return "../covers"


def cover_href(cover_path: str, *, version: str | None = None) -> str:
    segment = cover_public_segment(cover_path)
    if not segment:
        return ""
    url = f"{covers_static_prefix()}/{segment}"
    if version:
        url += f"?v={quote(version, safe='')}"
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


def delete_book_action() -> str:
    return cgi_script("delete_book.py")


def admin_mode_action() -> str:
    return cgi_script("admin_mode.py")


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
    conn.execute("PRAGMA cache_size = -64000")
    conn.execute("PRAGMA mmap_size = 268435456")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def connect_rw() -> sqlite3.Connection:
    db = database_path()
    if not db.exists():
        raise FileNotFoundError(f"Library database not found: {db}")
    try:
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        configure_sqlite_connection(conn)
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
    keys = row.keys()

    def col(name: str, default: object = None) -> object:
        return row[name] if name in keys else default

    return BookRow(
        id=row["id"],
        file_path=str(col("file_path", "")),
        file_name=row["file_name"],
        format=row["format"],
        file_size=row["file_size"],
        file_mtime=float(col("file_mtime", 0)),
        content_hash=col("content_hash"),
        title=col("title"),
        sort_title=col("sort_title"),
        authors=col("authors"),
        publisher=col("publisher"),
        published_date=col("published_date"),
        isbn=col("isbn"),
        language=col("language"),
        description=col("description"),
        series=col("series"),
        series_index=col("series_index"),
        page_count=col("page_count"),
        cover_path=col("cover_path"),
        tags=col("tags"),
        first_seen_at=str(col("first_seen_at", "")),
        last_scanned_at=str(col("last_scanned_at", "")),
        is_missing=int(col("is_missing", 0)),
    )


_LIST_BOOK_COLUMNS = (
    "books.id",
    "books.file_name",
    "books.format",
    "books.file_size",
    "books.title",
    "books.sort_title",
    "books.authors",
    "books.series",
    "books.series_index",
    "books.cover_path",
    "books.is_missing",
    "books.last_scanned_at",
)
_LIST_BOOK_SELECT = ", ".join(_LIST_BOOK_COLUMNS)


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


LIB_BROWSE_PREFIX = "lib_"


def _form_field(form, name: str, *, prefix: str = "", default: str = "") -> str:
    value = form.getfirst(f"{prefix}{name}", default)
    return value if value is not None else default


def parse_library_browse_context_from_form(
    form,
    *,
    current_user: UserRow | None = None,
    prefix: str = "",
) -> LibraryBrowseContext:
    favorites_only = (
        _form_field(form, "favorites", prefix=prefix) == "1"
        and current_user is not None
    )
    return parse_library_browse_context(
        title=_form_field(form, "title", prefix=prefix),
        author=_form_field(form, "author", prefix=prefix),
        publisher=_form_field(form, "publisher", prefix=prefix),
        genre=_form_field(form, "genre", prefix=prefix),
        language=_form_field(form, "language", prefix=prefix),
        sort=_form_field(form, "sort", prefix=prefix, default="title") or "title",
        sort_dir=_form_field(form, "sort_dir", prefix=prefix),
        page_size=_form_field(form, "page_size", prefix=prefix),
        page=_form_field(form, "page", prefix=prefix, default="1") or "1",
        favorites_only=favorites_only,
    )


def parse_stored_neighbor_ids(
    form,
    *,
    prefix: str = LIB_BROWSE_PREFIX,
) -> tuple[int | None, int | None]:
    """Neighbor ids captured before an edit (pre-sort-position navigation)."""
    prev_raw = _form_field(form, "prev_id", prefix=prefix)
    next_raw = _form_field(form, "next_id", prefix=prefix)
    prev_id = int(prev_raw) if prev_raw.isdigit() else None
    next_id = int(next_raw) if next_raw.isdigit() else None
    return prev_id, next_id


def browse_context_hidden_inputs(
    ctx: LibraryBrowseContext,
    *,
    prev_book_id: int | None = None,
    next_book_id: int | None = None,
    prefix: str = LIB_BROWSE_PREFIX,
) -> str:
    """Hidden fields for POST round-trips (lib_ prefix avoids clashing with edit fields)."""
    lines: list[str] = []
    if prev_book_id is not None:
        lines.append(f'<input type="hidden" name="{prefix}prev_id" value="{prev_book_id}">')
    if next_book_id is not None:
        lines.append(f'<input type="hidden" name="{prefix}next_id" value="{next_book_id}">')
    for key, value in ctx.normalized().query_params().items():
        if key == "id":
            continue
        lines.append(
            f'<input type="hidden" name="{prefix}{key}" value="{esc(value)}">'
        )
    return "\n".join(lines)


def book_detail_navigation_from_form(
    conn: sqlite3.Connection,
    book_id: int,
    form,
    *,
    current_user: UserRow | None,
    use_stored_neighbors: bool = True,
) -> tuple[LibraryBrowseContext, int | None, int | None]:
    """Browse context and neighbors for detail pages after a POST round-trip."""
    browse_ctx = parse_library_browse_context_from_form(
        form, current_user=current_user, prefix=LIB_BROWSE_PREFIX
    )
    if use_stored_neighbors:
        prev_book_id, next_book_id = parse_stored_neighbor_ids(form)
    else:
        prev_book_id, next_book_id = neighbor_book_ids(
            conn,
            book_id,
            browse_ctx,
            user_id=current_user.id if current_user else None,
        )
    return browse_ctx, prev_book_id, next_book_id


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


def _sort_key_parts(sort: str, sort_dir: str) -> list[tuple[str, str]]:
    """Sort key expressions and directions; final tie-breaker is books.id ASC."""
    direction = normalize_sort_dir(sort, sort_dir)
    if sort == "published":
        return [
            ("books.published_date IS NULL", "asc"),
            ("books.published_date", direction),
            ("books.id", "asc"),
        ]
    if sort == "pages":
        return [
            ("books.page_count IS NULL", "asc"),
            ("books.page_count", direction),
            ("books.id", "asc"),
        ]
    column = SORT_COLUMNS.get(sort, SORT_COLUMNS["title"])
    return [(column, direction), ("books.id", "asc")]


def _fetch_sort_key_values(
    conn: sqlite3.Connection,
    book_id: int,
    parts: list[tuple[str, str]],
) -> list[object] | None:
    selects = ", ".join(f"{expr} AS sk{i}" for i, (expr, _) in enumerate(parts))
    row = conn.execute(
        f"SELECT {selects} FROM books WHERE id = ? AND is_missing = 0",
        (book_id,),
    ).fetchone()
    if row is None:
        return None
    return [row[i] for i in range(len(parts))]


def _list_order_compare_sql(
    parts: list[tuple[str, str]],
    anchor_values: list[object],
    *,
    after: bool,
) -> tuple[str, list[object]]:
    """SQL condition for rows strictly after/before anchor in library list order."""
    conditions: list[str] = []
    params: list[object] = []

    for i, (expr, direction) in enumerate(parts):
        op = ">" if (direction == "asc") == after else "<"
        prefix_parts: list[str] = []
        for j in range(i):
            ej, _ = parts[j]
            anchor_j = anchor_values[j]
            prefix_parts.append(f"(({ej} IS NULL AND ? IS NULL) OR ({ej} = ?))")
            params.extend([anchor_j, anchor_j])

        if prefix_parts:
            prefix_sql = " AND ".join(prefix_parts)
            conditions.append(f"({prefix_sql} AND {expr} {op} ?)")
        else:
            conditions.append(f"({expr} {op} ?)")
        params.append(anchor_values[i])

    return "(" + " OR ".join(conditions) + ")", params


def _order_sql(parts: list[tuple[str, str]], *, reverse: bool) -> str:
    bits: list[str] = []
    for expr, direction in parts:
        dir_value = direction
        if reverse:
            dir_value = "desc" if direction == "asc" else "asc"
        bits.append(f"{expr} {dir_value.upper()}")
    return ", ".join(bits)


def _neighbor_id_query(
    fts_match: str | None,
    where: list[str],
    params: list[object],
    parts: list[tuple[str, str]],
    anchor_values: list[object],
    *,
    after: bool,
) -> tuple[str, list[object]]:
    compare_sql, compare_params = _list_order_compare_sql(
        parts,
        anchor_values,
        after=after,
    )
    order_sql = _order_sql(parts, reverse=not after)
    where_sql = " AND ".join(where)

    if fts_match:
        sql = (
            "SELECT books.id FROM books "
            "INNER JOIN books_fts ON books_fts.rowid = books.id "
            f"WHERE books_fts MATCH ? AND {where_sql} AND {compare_sql} "
            f"ORDER BY {order_sql} LIMIT 1"
        )
        query_params: list[object] = [fts_match, *params, *compare_params]
    else:
        sql = (
            f"SELECT books.id FROM books WHERE {where_sql} AND {compare_sql} "
            f"ORDER BY {order_sql} LIMIT 1"
        )
        query_params = [*params, *compare_params]
    return sql, query_params


def _book_in_filtered_view(
    conn: sqlite3.Connection,
    book_id: int,
    fts_match: str | None,
    where: list[str],
    params: list[object],
) -> bool:
    where_sql = " AND ".join([*where, "books.id = ?"])
    if fts_match:
        sql = (
            "SELECT 1 FROM books "
            "INNER JOIN books_fts ON books_fts.rowid = books.id "
            f"WHERE books_fts MATCH ? AND {where_sql} LIMIT 1"
        )
        query_params: list[object] = [fts_match, *params, book_id]
    else:
        sql = f"SELECT 1 FROM books WHERE {where_sql} LIMIT 1"
        query_params = [*params, book_id]
    return conn.execute(sql, query_params).fetchone() is not None


@dataclass(frozen=True)
class FilterOptions:
    languages: list[str]


def load_filter_options(conn: sqlite3.Connection) -> FilterOptions:
    return FilterOptions(languages=cached_languages(conn))


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


def favorite_book_ids(conn: sqlite3.Connection, user_id: int) -> frozenset[int]:
    rows = conn.execute(
        "SELECT book_id FROM user_favorites WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    return frozenset(int(row["book_id"]) for row in rows)


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


def is_admin_user(user: UserRow | None) -> bool:
    return is_admin_username(user.username if user is not None else None)


def admin_mode_enabled() -> bool:
    cookie_header = os.environ.get("HTTP_COOKIE", "")
    return parse_cookie_header(cookie_header, ADMIN_MODE_COOKIE) == "1"


def is_admin(user: UserRow | None) -> bool:
    return is_admin_user(user) and admin_mode_enabled()


def _book_filter_clause(
    conn: sqlite3.Connection,
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
    use_author_tokens = author_tokens_available(conn)
    fts_match = build_fts_match(
        title=title,
        publisher=publisher,
        genre=genre,
    )

    if author.strip() and use_author_tokens:
        append_author_token_filters(where, params, author)
    elif author.strip():
        author_clause = fts_field_match(["authors"], search_words(author))
        if author_clause:
            fts_match = (
                f"{fts_match} AND {author_clause}" if fts_match else author_clause
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
        if author.strip() and not use_author_tokens:
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
            f"SELECT {_LIST_BOOK_SELECT} FROM books "
            "INNER JOIN books_fts ON books_fts.rowid = books.id "
            f"WHERE books_fts MATCH ? AND {where_sql} "
            f"ORDER BY {order_by}, books.id"
        )
        query_params: list[object] = [fts_match, *params]
    else:
        sql = (
            f"SELECT {_LIST_BOOK_SELECT} FROM books WHERE {where_sql} "
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
) -> tuple[list[BookRow], int, int, int, FilterOptions, bool]:
    """Return books, filtered_count, library_total, page, options, count_exact."""
    sort_dir = normalize_sort_dir(sort, sort_dir)
    page_size = normalize_page_size(page_size)
    library_total = cached_library_total(conn)
    options = load_filter_options(conn)

    fts_match, where, params, order_by = _book_filter_clause(
        conn,
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

    filtered = has_search_filters(
        title=title,
        author=author,
        publisher=publisher,
        genre=genre,
        language=language,
        favorites_only=favorites_only,
    )
    page = max(1, page)
    count_exact = True

    if sort == "random":
        if fts_match:
            count_exact = False
            filtered_count = page_size
        elif not filtered:
            filtered_count = library_total
        else:
            count_sql, count_params = _filtered_count_sql(fts_match, where, params)
            filtered_count = int(conn.execute(count_sql, count_params).fetchone()[0])

        limit = min(page_size, filtered_count) if filtered_count else 0
        if limit == 0:
            return [], filtered_count, library_total, page, options, count_exact
        if fts_match:
            random_sql = (
                f"SELECT {_LIST_BOOK_SELECT} FROM books "
                "INNER JOIN books_fts ON books_fts.rowid = books.id "
                f"WHERE books_fts MATCH ? AND {' AND '.join(where)} "
                "ORDER BY RANDOM() LIMIT ?"
            )
            random_params: list[object] = [fts_match, *params, limit]
        else:
            random_sql = (
                f"SELECT {_LIST_BOOK_SELECT} FROM books WHERE {' AND '.join(where)} "
                "ORDER BY RANDOM() LIMIT ?"
            )
            random_params = [*params, limit]
        rows = conn.execute(random_sql, random_params).fetchall()
        return (
            [row_to_book(row) for row in rows],
            filtered_count,
            library_total,
            page,
            options,
            count_exact,
        )

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

    if not filtered:
        filtered_count = library_total
    else:
        if len(rows) < page_size:
            filtered_count = offset + len(rows)
            count_exact = True
        else:
            filtered_count = offset + len(rows)
            count_exact = False

    if count_exact:
        max_page = max(1, (filtered_count + page_size - 1) // page_size)
        if page > max_page:
            page = max_page

    return (
        [row_to_book(row) for row in rows],
        filtered_count,
        library_total,
        page,
        options,
        count_exact,
    )


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

    fts_match, where, params, _order_by = _book_filter_clause(
        conn,
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

    parts = _sort_key_parts(ctx.sort, ctx.sort_dir)
    anchor_values = _fetch_sort_key_values(conn, book_id, parts)
    if anchor_values is None:
        return None, None
    if not _book_in_filtered_view(conn, book_id, fts_match, where, params):
        return None, None

    prev_sql, prev_params = _neighbor_id_query(
        fts_match,
        where,
        params,
        parts,
        anchor_values,
        after=False,
    )
    next_sql, next_params = _neighbor_id_query(
        fts_match,
        where,
        params,
        parts,
        anchor_values,
        after=True,
    )
    prev_row = conn.execute(prev_sql, prev_params).fetchone()
    next_row = conn.execute(next_sql, next_params).fetchone()
    prev_id = int(prev_row[0]) if prev_row is not None else None
    next_id = int(next_row[0]) if next_row is not None else None
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
    if "authors" in fields:
        sync_author_tokens(conn, book_id, fields["authors"], commit=False)
    conn.commit()


class DeleteBookError(Exception):
    pass


def _remove_book_cover_files(book: BookRow) -> None:
    if book.cover_path:
        cover = project_root() / book.cover_path
        if cover.is_file():
            cover.unlink()
    covers_dir = project_root() / "data" / "covers"
    remove_cover_files(covers_dir, book.id)


def delete_book(conn: sqlite3.Connection, book: BookRow) -> None:
    """Delete ebook file, cover images, and database row for one book."""
    on_disk = Path(book.file_path).expanduser().resolve()
    book_file = allowed_book_file(book.file_path)
    if on_disk.is_file() and book_file is None:
        raise DeleteBookError(
            "Book file is outside configured library paths and cannot be deleted."
        )
    if book_file is not None:
        try:
            book_file.unlink()
        except OSError as exc:
            raise DeleteBookError(f"Could not delete book file: {exc}") from exc

    try:
        _remove_book_cover_files(book)
    except OSError as exc:
        raise DeleteBookError(f"Could not delete cover image: {exc}") from exc

    conn.execute("DELETE FROM books WHERE id = ?", (book.id,))
    conn.commit()
    refresh_library_stats(conn)


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
