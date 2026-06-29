from __future__ import annotations

from urllib.parse import urlencode

from exlibris.cgi.common import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_SORT_DIR,
    PAGE_SIZE_OPTIONS,
    BookRow,
    FilterOptions,
    LibraryBrowseContext,
    UserRow,
    book_detail_href,
    browse_context_hidden_inputs,
    cgi_script,
    cover_cache_version,
    cover_href,
    download_href,
    esc,
    edit_book_action,
    favorite_action,
    fetch_metadata_action,
    library_index_href,
    login_action,
    logout_action,
    register_action,
    restore_cover_action,
    format_published_date,
    format_size,
    has_search_filters,
    is_admin,
    normalize_page_size,
    normalize_sort_dir,
    static_asset,
    static_href,
)


def _header_auth(current_user: UserRow | None) -> str:
    if current_user is None:
        return f"""      <nav class="auth-nav">
        <a href="{esc(login_action())}">Log in</a>
        <a href="{esc(register_action())}">Create account</a>
      </nav>"""
    return f"""      <nav class="auth-nav">
        <span class="auth-nav__user">Signed in as {esc(current_user.username)}</span>
        <form class="auth-nav__logout" method="post" action="{esc(logout_action())}">
          <button type="submit">Log out</button>
        </form>
      </nav>"""


def page_shell(
    title: str,
    body: str,
    *,
    current_user: UserRow | None = None,
    body_attrs: str = "",
    scripts: str = "",
) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{esc(title)} · ExLibris</title>
  <link rel="stylesheet" href="{esc(static_href())}">
</head>
<body{body_attrs}>
  <header class="site-header">
    <div class="container header-inner">
      <a class="brand" href="{esc(cgi_script('index.py'))}">ExLibris</a>
      <span class="tagline">Your personal ebook library</span>
{_header_auth(current_user)}
    </div>
  </header>
  <main class="container">
{body}
  </main>
{scripts}</body>
</html>
"""


def _cover_img(book: BookRow, *, css_class: str = "book-cover") -> str:
    title = book.title or book.file_name
    if book.cover_path:
        version = cover_cache_version(book)
        return (
            f'<img class="{css_class}" src="{esc(cover_href(book.id, version=version))}" '
            f'alt="Cover: {esc(title)}" loading="lazy">'
        )
    initial = esc(title[0].upper() if title else "?")
    return f'<div class="{css_class} book-cover--placeholder" aria-hidden="true">{initial}</div>'


def _select_options(
    options: list[str],
    selected: str,
    empty_label: str,
    *,
    max_label: int | None = None,
) -> str:
    lines = [f'          <option value="">{esc(empty_label)}</option>']
    for option in options:
        is_selected = " selected" if option == selected else ""
        label = option
        if max_label is not None and len(option) > max_label:
            label = f"{option[: max_label - 1]}…"
        lines.append(
            f'          <option value="{esc(option)}"{is_selected} title="{esc(option)}">{esc(label)}</option>'
        )
    return "\n".join(lines)


def _filter_query(
    *,
    title: str,
    author: str,
    publisher: str,
    genre: str,
    language: str,
    sort: str,
    sort_dir: str,
    page_size: int,
    page: int | None = None,
    favorites_only: bool = False,
) -> str:
    params: dict[str, str] = {}
    if title:
        params["title"] = title
    if author:
        params["author"] = author
    if publisher:
        params["publisher"] = publisher
    if genre:
        params["genre"] = genre
    if language:
        params["language"] = language
    if favorites_only:
        params["favorites"] = "1"
    if sort and sort != "title":
        params["sort"] = sort
    if sort_dir != DEFAULT_SORT_DIR.get(sort, "asc"):
        params["sort_dir"] = sort_dir
    if page_size != DEFAULT_PAGE_SIZE:
        params["page_size"] = str(page_size)
    if page is not None and page > 1:
        params["page"] = str(page)
    query = urlencode(params)
    return f"?{query}" if query else ""


def _filter_hidden_inputs(
    *,
    title: str,
    author: str,
    publisher: str,
    genre: str,
    language: str,
    sort: str,
    sort_dir: str,
    page_size: int,
    favorites_only: bool = False,
) -> str:
    lines = [f'    <input type="hidden" name="sort_dir" value="{esc(sort_dir)}">']
    if favorites_only:
        lines.append('    <input type="hidden" name="favorites" value="1">')
    if title:
        lines.append(f'    <input type="hidden" name="title" value="{esc(title)}">')
    if author:
        lines.append(f'    <input type="hidden" name="author" value="{esc(author)}">')
    if publisher:
        lines.append(f'    <input type="hidden" name="publisher" value="{esc(publisher)}">')
    if genre:
        lines.append(f'    <input type="hidden" name="genre" value="{esc(genre)}">')
    if language:
        lines.append(f'    <input type="hidden" name="language" value="{esc(language)}">')
    if sort != "title":
        lines.append(f'    <input type="hidden" name="sort" value="{esc(sort)}">')
    if page_size != DEFAULT_PAGE_SIZE:
        lines.append(f'    <input type="hidden" name="page_size" value="{page_size}">')
    return "\n".join(lines)


def _page_size_options(selected: int) -> str:
    lines = []
    for size in PAGE_SIZE_OPTIONS:
        is_selected = " selected" if size == selected else ""
        lines.append(f'              <option value="{size}"{is_selected}>{size}</option>')
    return "\n".join(lines)


def _keyboard_help_dialog() -> str:
    return """    <dialog id="keyboard-help" class="keyboard-help">
      <h2>Keyboard shortcuts</h2>
      <dl class="keyboard-help__list">
        <dt><kbd>/</kbd></dt>
        <dd>Focus title search</dd>
        <dt><kbd>Esc</kbd></dt>
        <dd>Clear filters</dd>
        <dt><kbd>?</kbd></dt>
        <dd>Show this help</dd>
        <dt><kbd>←</kbd> / <kbd>→</kbd></dt>
        <dd>Previous / next page</dd>
        <dt><kbd>Page Up</kbd> / <kbd>Page Down</kbd></dt>
        <dd>Scroll the page</dd>
      </dl>
      <button type="button" class="keyboard-help__close" data-keyboard-help-close>Close</button>
    </dialog>
"""
def _format_count(count: int) -> str:
    return f"{count:,}"


def _sort_dir_controls(sort: str, sort_dir: str) -> str:
    if sort == "random":
        return ""
    asc_active = " sort-dir__btn--active" if sort_dir == "asc" else ""
    desc_active = " sort-dir__btn--active" if sort_dir == "desc" else ""
    return f"""            <div class="sort-dir" role="group" aria-label="Sort direction">
              <button type="button" class="sort-dir__btn{asc_active}" data-sort-dir="asc" aria-label="Sort ascending" title="Ascending">↑</button>
              <button type="button" class="sort-dir__btn{desc_active}" data-sort-dir="desc" aria-label="Sort descending" title="Descending">↓</button>
            </div>"""


def _pagination_nav(
    *,
    page: int,
    filtered_count: int,
    page_size: int,
    title: str,
    author: str,
    publisher: str,
    genre: str,
    language: str,
    sort: str,
    sort_dir: str,
    favorites_only: bool = False,
) -> str:
    base = cgi_script("index.py")
    common = dict(
        title=title,
        author=author,
        publisher=publisher,
        genre=genre,
        language=language,
        sort=sort,
        sort_dir=sort_dir,
        page_size=page_size,
        favorites_only=favorites_only,
    )
    hiddens = _filter_hidden_inputs(
        title=title,
        author=author,
        publisher=publisher,
        genre=genre,
        language=language,
        sort=sort,
        sort_dir=sort_dir,
        page_size=page_size,
        favorites_only=favorites_only,
    )

    if sort == "random":
        if filtered_count == 0:
            return ""

        prev_url = (
            base + _filter_query(**common, page=page - 1) if page > 1 else ""
        )
        next_url = base + _filter_query(**common, page=page + 1)

        attrs = [
            'class="pagination"',
            'aria-label="Random library pages"',
            "data-page-nav",
        ]
        if prev_url:
            attrs.append(f'data-prev-url="{esc(prev_url)}"')
        attrs.append(f'data-next-url="{esc(next_url)}"')

        prev_link = ""
        if prev_url:
            prev_link = (
                f'      <a class="pagination__link" href="{esc(prev_url)}">← Previous</a>\n'
            )

        next_link = (
            f'      <a class="pagination__link" href="{esc(next_url)}">Next →</a>\n'
        )

        return f"""    <nav {" ".join(attrs)}>
{prev_link}      <span class="pagination__status">Page {page} · Random sample · ← → for another</span>
{next_link}    </nav>
"""

    if filtered_count <= page_size:
        return ""

    max_page = (filtered_count + page_size - 1) // page_size
    prev_url = base + _filter_query(**common, page=page - 1) if page > 1 else ""
    next_url = (
        base + _filter_query(**common, page=page + 1) if page < max_page else ""
    )

    attrs = [
        'class="pagination"',
        'aria-label="Search results pages"',
        "data-page-nav",
    ]
    if prev_url:
        attrs.append(f'data-prev-url="{esc(prev_url)}"')
    if next_url:
        attrs.append(f'data-next-url="{esc(next_url)}"')

    prev_link = ""
    if prev_url:
        prev_link = (
            f'      <a class="pagination__link" href="{esc(prev_url)}">← Previous</a>\n'
        )

    next_link = ""
    if next_url:
        next_link = (
            f'      <a class="pagination__link" href="{esc(next_url)}">Next →</a>\n'
        )

    jump_form = f"""      <form class="pagination__jump" method="get" action="{esc(base)}">
{hiddens}
        <label class="pagination__jump-label">
          Page
          <input class="pagination__jump-input" type="number" name="page" min="1" max="{max_page}" value="{page}" aria-label="Jump to page">
        </label>
        <button type="submit" class="pagination__jump-btn">Go</button>
      </form>
"""

    return f"""    <nav {" ".join(attrs)}>
{prev_link}      <span class="pagination__status">Page {page} of {max_page} · ← → to browse</span>
{jump_form}{next_link}    </nav>
"""


def render_library(
    books: list[BookRow],
    filtered_count: int,
    library_total: int,
    page: int,
    options: FilterOptions,
    *,
    selected_title: str,
    selected_author: str,
    selected_publisher: str,
    selected_genre: str,
    selected_language: str,
    sort: str,
    sort_dir: str,
    page_size: int,
    favorites_only: bool = False,
    current_user: UserRow | None = None,
) -> str:
    sort_dir = normalize_sort_dir(sort, sort_dir)
    page_size = normalize_page_size(page_size)
    browse_ctx = LibraryBrowseContext(
        title=selected_title,
        author=selected_author,
        publisher=selected_publisher,
        genre=selected_genre,
        language=selected_language,
        sort=sort,
        sort_dir=sort_dir,
        page_size=page_size,
        page=page,
        favorites_only=favorites_only,
    )
    sort_selected = {
        key: " selected" if sort == key else ""
        for key in ("title", "author", "published", "size", "pages", "scanned", "random")
    }

    if books:
        cards = "\n".join(_book_card(book, browse_ctx) for book in books)
        pagination = _pagination_nav(
            page=page,
            filtered_count=filtered_count,
            page_size=page_size,
            title=selected_title,
            author=selected_author,
            publisher=selected_publisher,
            genre=selected_genre,
            language=selected_language,
            sort=sort,
            sort_dir=sort_dir,
            favorites_only=favorites_only,
        )
        collection = f"""{pagination}    <ul class="book-grid">
{cards}
    </ul>
{pagination}"""
    elif library_total == 0:
        collection = """    <div class="empty-state">
      <h2>No books in library</h2>
      <p>Run <code>python scan_books.py</code> or <code>exlibris scan</code> to index your collection.</p>
    </div>"""
    else:
        collection = """    <div class="empty-state">
      <h2>No books found</h2>
      <p>Try different search terms or clear filters.</p>
    </div>"""

    filtered = has_search_filters(
        title=selected_title,
        author=selected_author,
        publisher=selected_publisher,
        genre=selected_genre,
        language=selected_language,
        favorites_only=favorites_only,
    )

    if sort == "random" and filtered_count:
        shown = len(books)
        if filtered:
            stats = (
                f"{shown:,} random books from {filtered_count:,} matches "
                f"· {_format_count(library_total)} in library"
            )
        else:
            stats = (
                f"{shown:,} random books · {_format_count(library_total)} in library"
            )
    elif filtered_count:
        start = (page - 1) * page_size + 1
        end = min(page * page_size, filtered_count)
        favorites_note = " · favorites" if favorites_only else ""
        if filtered:
            stats = (
                f"Showing {start:,}–{end:,} of {filtered_count:,} matches"
                f"{favorites_note} · {_format_count(library_total)} in library"
            )
        else:
            stats = (
                f"Showing {start:,}–{end:,} of {_format_count(library_total)} in library"
                f"{favorites_note}"
            )
    elif library_total == 0:
        stats = "No books indexed yet"
    else:
        stats = f"No matches · {_format_count(library_total)} in library"

    pagination_script = f'\n    <script src="{esc(static_asset("library.js"))}"></script>'

    clear_url = esc(
        cgi_script("index.py")
        + _filter_query(
            title="",
            author="",
            publisher="",
            genre="",
            language="",
            sort="title",
            sort_dir=DEFAULT_SORT_DIR["title"],
            page_size=DEFAULT_PAGE_SIZE,
            favorites_only=False,
        )
    )
    favorites_checked = " checked" if favorites_only else ""
    favorites_filter = ""
    if current_user is not None:
        favorites_filter = f"""          <label class="filter-favorites">
            <input type="checkbox" name="favorites" value="1"{favorites_checked} data-filter-auto>
            Favorites only
          </label>
"""
    body = f"""    <section class="toolbar">
      <form id="library-filter-form" class="filter-form filter-form--compact" method="get" action="{esc(cgi_script('index.py'))}" data-clear-url="{clear_url}">
        <input type="hidden" name="sort_dir" value="{esc(sort_dir)}">
        <div class="filter-form__main">
          <input class="filter-input" type="search" id="search-title" name="title" value="{esc(selected_title)}" placeholder="Title" aria-label="Filter by title" autocomplete="off" data-filter-search>
          <input class="filter-input" type="search" name="author" value="{esc(selected_author)}" placeholder="Author" aria-label="Filter by author" autocomplete="off" data-filter-search>
          <input class="filter-input" type="search" name="publisher" value="{esc(selected_publisher)}" placeholder="Publisher" aria-label="Filter by publisher" autocomplete="off" data-filter-search>
          <input class="filter-input" type="search" name="genre" value="{esc(selected_genre)}" placeholder="Genre" aria-label="Filter by genre" autocomplete="off" data-filter-search>
          <select class="filter-input" name="language" aria-label="Filter by language" data-filter-auto>
{_select_options(options.languages, selected_language, "Language", max_label=10)}
          </select>
{favorites_filter}          <div class="sort-controls">
            <select class="filter-input" name="sort" aria-label="Sort by" data-filter-auto>
              <option value="title"{sort_selected["title"]}>Title</option>
              <option value="author"{sort_selected["author"]}>Author</option>
              <option value="published"{sort_selected["published"]}>Published</option>
              <option value="size"{sort_selected["size"]}>Size</option>
              <option value="pages"{sort_selected["pages"]}>Pages</option>
              <option value="scanned"{sort_selected["scanned"]}>Scanned</option>
              <option value="random"{sort_selected["random"]}>Random</option>
            </select>
{_sort_dir_controls(sort, sort_dir)}
          </div>
          <select class="filter-input filter-input--narrow" name="page_size" aria-label="Books per page" data-filter-auto>
{_page_size_options(page_size)}
          </select>
          <div class="filter-form__buttons">
            <button type="submit">Apply</button>
            <a class="filter-clear" href="{clear_url}">Clear</a>
            <button type="button" class="filter-help" data-keyboard-help-open title="Keyboard shortcuts (?)">?</button>
          </div>
        </div>
        <p class="stats">{stats} · <kbd>?</kbd> shortcuts</p>
      </form>
    </section>

{collection}
{_keyboard_help_dialog()}{pagination_script}
"""
    return page_shell("Library", body, current_user=current_user)


def render_login(*, next_url: str = "", error: str = "") -> str:
    flash = ""
    if error:
        flash = f"""      <p class="flash flash--error">{esc(error)}</p>
"""
    next_input = ""
    register_href = register_action()
    if next_url:
        next_input = f'        <input type="hidden" name="next" value="{esc(next_url)}">\n'
        register_href = f"{register_action()}?{urlencode({'next': next_url})}"
    body = f"""    <section class="auth-panel">
      <h1>Log in</h1>
{flash}      <form class="auth-form" method="post" action="{esc(login_action())}">
{next_input}        <label>
          Username
          <input class="filter-input" type="text" name="username" autocomplete="username" required>
        </label>
        <label>
          Password
          <input class="filter-input" type="password" name="password" autocomplete="current-password" required>
        </label>
        <button type="submit" class="button button--download">Log in</button>
      </form>
      <p class="auth-panel__hint">No account yet? <a href="{esc(register_href)}">Create one</a> · <a href="{esc(cgi_script('index.py'))}">← Back to library</a></p>
    </section>
"""
    return page_shell("Log in", body)


def render_register(*, next_url: str = "", error: str = "", username: str = "") -> str:
    flash = ""
    if error:
        flash = f"""      <p class="flash flash--error">{esc(error)}</p>
"""
    next_input = ""
    login_href = login_action()
    if next_url:
        next_input = f'        <input type="hidden" name="next" value="{esc(next_url)}">\n'
        login_href = f"{login_action()}?{urlencode({'next': next_url})}"
    body = f"""    <section class="auth-panel">
      <h1>Create account</h1>
      <p class="auth-panel__intro">Accounts are only needed to save favorites. Browsing the library does not require an account.</p>
{flash}      <form class="auth-form" method="post" action="{esc(register_action())}">
{next_input}        <label>
          Username
          <input class="filter-input" type="text" name="username" value="{esc(username)}" autocomplete="username" required maxlength="64">
        </label>
        <label>
          Password
          <input class="filter-input" type="password" name="password" autocomplete="new-password" required>
        </label>
        <label>
          Confirm password
          <input class="filter-input" type="password" name="password_confirm" autocomplete="new-password" required>
        </label>
        <button type="submit" class="button button--download">Create account</button>
      </form>
      <p class="auth-panel__hint">Already have an account? <a href="{esc(login_href)}">Log in</a> · <a href="{esc(cgi_script('index.py'))}">← Back to library</a></p>
    </section>
"""
    return page_shell("Create account", body)


def _book_card(book: BookRow, browse_ctx: LibraryBrowseContext) -> str:
    title = book.title or book.file_name
    author = book.authors or "Unknown author"
    series = ""
    if book.series:
        index = f" #{book.series_index:g}" if book.series_index is not None else ""
        series = f'<p class="book-card__series">{esc(book.series)}{esc(index)}</p>'

    missing = ' book-card--missing' if book.is_missing else ""
    cover = _cover_img(book)
    detail_href = book_detail_href(book.id, browse_ctx)
    return f"""      <li class="book-card{missing}">
        <a class="book-card__link" href="{esc(detail_href)}">
          {cover}
          <div class="book-card__body">
            <span class="badge badge--{esc(book.format)}">{esc(book.format.upper())}</span>
            <h2 class="book-card__title">{esc(title)}</h2>
            <p class="book-card__author">{esc(author)}</p>
            {series}
            <p class="book-card__meta">{esc(format_size(book.file_size))}</p>
          </div>
        </a>
      </li>"""


def render_book_detail(
    book: BookRow,
    *,
    browse_ctx: LibraryBrowseContext | None = None,
    prev_book_id: int | None = None,
    next_book_id: int | None = None,
    notice: str = "",
    error: str = "",
    current_user: UserRow | None = None,
    is_favorite: bool = False,
) -> str:
    title = book.title or book.file_name
    authors_value = book.authors or ""
    genre_value = book.tags or ""
    user_is_admin = is_admin(current_user)
    ctx = (browse_ctx or LibraryBrowseContext()).normalized()
    back_url = library_index_href(ctx)
    prev_url = book_detail_href(prev_book_id, ctx) if prev_book_id else ""
    next_url = book_detail_href(next_book_id, ctx) if next_book_id else ""
    body_attrs = ""
    scripts = ""
    if prev_url or next_url:
        attrs: list[str] = []
        if prev_url:
            attrs.append(f'data-book-prev-url="{esc(prev_url)}"')
        if next_url:
            attrs.append(f'data-book-next-url="{esc(next_url)}"')
        body_attrs = " " + " ".join(attrs)
        scripts = f'    <script src="{esc(static_asset("detail.js"))}"></script>\n'
    series_block = ""
    if book.series:
        index = f" #{book.series_index:g}" if book.series_index is not None else ""
        series_block = f"""        <div>
          <dt>Series</dt>
          <dd>{esc(book.series)}{esc(index)}</dd>
        </div>
"""

    optional_fields = [
        ("Publisher", book.publisher),
        ("Published", format_published_date(book.published_date)),
        ("ISBN", book.isbn),
        ("Language", book.language),
        ("Genre", book.tags),
        ("Pages", str(book.page_count) if book.page_count is not None else None),
        ("File name", book.file_name),
    ]
    if user_is_admin:
        optional_fields = [
            field for field in optional_fields if field[0] != "Genre"
        ]
    meta_items = "\n".join(
        f"""        <div>
          <dt>{esc(label)}</dt>
          <dd>{esc(value)}</dd>
        </div>"""
        for label, value in optional_fields
        if value
    )

    description = ""
    if book.description:
        description = f"""      <section class="description">
        <h2>Description</h2>
        <div class="description__body">{book.description}</div>
      </section>
"""

    flash = ""
    if notice:
        flash = f"""      <p class="flash flash--notice">{esc(notice)}</p>
"""
    elif error:
        flash = f"""      <p class="flash flash--error">{esc(error)}</p>
"""

    restore_cover_form = ""
    if not book.is_missing:
        restore_cover_form = f"""              <form class="book-actions__form book-actions__form--restore" method="post" action="{esc(restore_cover_action())}" onsubmit="return confirm('Restore the cover embedded in this ebook? The current cover image will be replaced.');">
                <input type="hidden" name="id" value="{book.id}">
                <button type="submit" class="button button--restore">Restore cover from file</button>
              </form>
"""

    favorite_form = ""
    if current_user is not None:
        checked = " checked" if is_favorite else ""
        favorite_form = f"""            <form class="favorite-form" method="post" action="{esc(favorite_action())}">
              <input type="hidden" name="id" value="{book.id}">
              <input type="hidden" name="favorite" value="0">
              <label class="favorite-toggle">
                <input type="checkbox" name="favorite" value="1"{checked} onchange="this.form.submit()">
                Favorite
              </label>
            </form>
"""
    else:
        favorite_form = f"""            <p class="favorite-login"><a href="{esc(login_action())}?next=book.py%3Fid%3D{book.id}">Log in</a> or <a href="{esc(register_action())}?next=book.py%3Fid%3D{book.id}">create an account</a> to save favorites</p>
"""

    if user_is_admin:
        browse_hidden = browse_context_hidden_inputs(
            ctx,
            prev_book_id=prev_book_id,
            next_book_id=next_book_id,
        )
        title_author_block = f"""            <form class="book-edit-form" method="post" action="{esc(edit_book_action())}">
              <input type="hidden" name="id" value="{book.id}">
              {browse_hidden}
              <div class="book-edit-form__fields">
                <label class="book-edit-form__label">
                  <span class="book-edit-form__name">Title</span>
                  <input class="filter-input book-edit-form__input" type="text" name="title" value="{esc(title)}" required maxlength="500">
                </label>
                <label class="book-edit-form__label">
                  <span class="book-edit-form__name">Author</span>
                  <input class="filter-input book-edit-form__input" type="text" name="authors" value="{esc(authors_value)}" maxlength="500">
                </label>
                <label class="book-edit-form__label">
                  <span class="book-edit-form__name">Genre</span>
                  <input class="filter-input book-edit-form__input" type="text" name="genre" value="{esc(genre_value)}" maxlength="500">
                </label>
              </div>
              <button type="submit" class="button button--fetch">Save metadata</button>
            </form>
"""
    else:
        author_display = esc(authors_value) if authors_value else "Unknown author"
        title_author_block = f"""            <h1 class="book-detail__title">{esc(title)}</h1>
            <p class="book-detail__author">{author_display}</p>
"""

    body = f"""    <p class="back-link"><a href="{esc(back_url)}">← Back to library</a></p>
{flash}
    <article class="book-detail">
      <div class="book-detail__layout">
        {_cover_img(book, css_class="book-cover book-cover--large")}
        <div class="book-detail__content">
          <header class="book-detail__header">
            <span class="badge badge--{esc(book.format)}">{esc(book.format.upper())}</span>
{title_author_block}
{favorite_form}            <p class="book-actions">
              <a class="button button--download" href="{esc(download_href(book.id))}">Download</a>
              <form class="book-actions__form book-actions__form--fetch" method="post" action="{esc(fetch_metadata_action())}" onsubmit="var b=this.querySelector('button');b.disabled=true;b.textContent='Fetching…';">
                <input type="hidden" name="id" value="{book.id}">
                <button type="submit" class="button button--fetch">Fetch metadata online</button>
              </form>
              {restore_cover_form}
              <span class="book-actions__meta">{esc(format_size(book.file_size))}</span>
            </p>
          </header>

          <dl class="meta-grid">
{series_block}{meta_items}
      </dl>

{description}        </div>
      </div>
    </article>
"""
    return page_shell(
        title,
        body,
        current_user=current_user,
        body_attrs=body_attrs,
        scripts=scripts,
    )


def render_error(message: str, *, status_hint: str = "Error") -> str:
    body = f"""    <div class="empty-state">
      <h2>{esc(status_hint)}</h2>
      <p>{esc(message)}</p>
      <p><a href="{esc(cgi_script('index.py'))}">Return to library</a></p>
    </div>
"""
    return page_shell(status_hint, body)
