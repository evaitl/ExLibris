from __future__ import annotations

from urllib.parse import urlencode

from exlibris.cgi.common import (
    PAGE_SIZE,
    BookRow,
    FilterOptions,
    cgi_script,
    cover_href,
    download_href,
    esc,
    fetch_metadata_action,
    format_published_date,
    format_size,
    has_search_filters,
    static_asset,
    static_href,
)


def page_shell(title: str, body: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{esc(title)} · ExLibris</title>
  <link rel="stylesheet" href="{esc(static_href())}">
</head>
<body>
  <header class="site-header">
    <div class="container header-inner">
      <a class="brand" href="{esc(cgi_script('index.py'))}">ExLibris</a>
      <p class="tagline">Your personal ebook library</p>
    </div>
  </header>
  <main class="container">
{body}
  </main>
</body>
</html>
"""


def _cover_img(book: BookRow, *, css_class: str = "book-cover") -> str:
    title = book.title or book.file_name
    if book.cover_path:
        version = book.last_scanned_at.replace(":", "").replace("-", "")
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
    page: int | None = None,
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
    if sort and sort != "title":
        params["sort"] = sort
    if page is not None and page > 1:
        params["page"] = str(page)
    query = urlencode(params)
    return f"?{query}" if query else ""


def _format_count(count: int) -> str:
    return f"{count:,}"


def _pagination_nav(
    *,
    page: int,
    filtered_count: int,
    title: str,
    author: str,
    publisher: str,
    genre: str,
    language: str,
    sort: str,
) -> str:
    if sort == "random":
        if filtered_count == 0:
            return ""

        base = cgi_script("index.py")
        common = dict(
            title=title,
            author=author,
            publisher=publisher,
            genre=genre,
            language=language,
            sort=sort,
        )
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
{prev_link}      <span class="pagination__status">Page {page} · Random sample · Page Up/Down for another</span>
{next_link}    </nav>
"""

    if filtered_count <= PAGE_SIZE:
        return ""

    max_page = (filtered_count + PAGE_SIZE - 1) // PAGE_SIZE
    base = cgi_script("index.py")
    common = dict(
        title=title,
        author=author,
        publisher=publisher,
        genre=genre,
        language=language,
        sort=sort,
    )

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

    return f"""    <nav {" ".join(attrs)}>
{prev_link}      <span class="pagination__status">Page {page} of {max_page} · Page Up/Down to browse</span>
{next_link}    </nav>
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
) -> str:
    sort_selected = {
        key: " selected" if sort == key else ""
        for key in ("title", "author", "published", "size", "scanned", "random")
    }

    if books:
        cards = "\n".join(_book_card(book) for book in books)
        pagination = _pagination_nav(
            page=page,
            filtered_count=filtered_count,
            title=selected_title,
            author=selected_author,
            publisher=selected_publisher,
            genre=selected_genre,
            language=selected_language,
            sort=sort,
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
        start = (page - 1) * PAGE_SIZE + 1
        end = min(page * PAGE_SIZE, filtered_count)
        if filtered:
            stats = (
                f"Showing {start:,}–{end:,} of {filtered_count:,} matches "
                f"· {_format_count(library_total)} in library"
            )
        else:
            stats = f"Showing {start:,}–{end:,} of {_format_count(library_total)} in library"
    elif library_total == 0:
        stats = "No books indexed yet"
    else:
        stats = f"No matches · {_format_count(library_total)} in library"

    pagination_script = ""
    if filtered_count and (sort == "random" or filtered_count > PAGE_SIZE):
        pagination_script = (
            f'\n    <script src="{esc(static_asset("library.js"))}"></script>'
        )

    body = f"""    <section class="toolbar">
      <form class="filter-form" method="get" action="{esc(cgi_script('index.py'))}">
        <div class="filter-form__row">
          <label class="filter-field">
            <span class="filter-field__label">Title</span>
            <input type="search" name="title" value="{esc(selected_title)}" placeholder="Search titles…" aria-label="Filter by title">
          </label>
          <label class="filter-field">
            <span class="filter-field__label">Author</span>
            <input type="search" name="author" value="{esc(selected_author)}" placeholder="Search authors…" aria-label="Filter by author">
          </label>
          <label class="filter-field">
            <span class="filter-field__label">Publisher</span>
            <input type="search" name="publisher" value="{esc(selected_publisher)}" placeholder="Search publishers…" aria-label="Filter by publisher">
          </label>
          <label class="filter-field">
            <span class="filter-field__label">Genre</span>
            <input type="search" name="genre" value="{esc(selected_genre)}" placeholder="Search genres…" aria-label="Filter by genre">
          </label>
          <label class="filter-field">
            <span class="filter-field__label">Language</span>
            <select name="language" aria-label="Filter by language">
{_select_options(options.languages, selected_language, "All languages")}
            </select>
          </label>
        </div>
        <div class="filter-form__row filter-form__row--actions">
          <label class="filter-field">
            <span class="filter-field__label">Sort</span>
            <select name="sort" aria-label="Sort by">
              <option value="title"{sort_selected["title"]}>Title</option>
              <option value="author"{sort_selected["author"]}>Author</option>
              <option value="published"{sort_selected["published"]}>Published date</option>
              <option value="size"{sort_selected["size"]}>Size</option>
              <option value="scanned"{sort_selected["scanned"]}>Last scanned</option>
              <option value="random"{sort_selected["random"]}>Random</option>
            </select>
          </label>
          <div class="filter-form__buttons">
            <button type="submit">Apply filters</button>
            <a class="filter-clear" href="{esc(cgi_script('index.py'))}">Clear</a>
          </div>
        </div>
      </form>
      <p class="stats">{stats}</p>
    </section>

{collection}{pagination_script}
"""
    return page_shell("Library", body)


def _book_card(book: BookRow) -> str:
    title = book.title or book.file_name
    author = book.authors or "Unknown author"
    series = ""
    if book.series:
        index = f" #{book.series_index:g}" if book.series_index is not None else ""
        series = f'<p class="book-card__series">{esc(book.series)}{esc(index)}</p>'

    missing = ' book-card--missing' if book.is_missing else ""
    cover = _cover_img(book)
    return f"""      <li class="book-card{missing}">
        <a class="book-card__link" href="{esc(cgi_script('book.py'))}?id={book.id}">
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
    notice: str = "",
    error: str = "",
) -> str:
    title = book.title or book.file_name
    subtitle = (
        f'      <p class="subtitle">by {esc(book.authors)}</p>\n' if book.authors else ""
    )
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

    body = f"""    <p class="back-link"><a href="{esc(cgi_script('index.py'))}">← Back to library</a></p>
{flash}
    <article class="book-detail">
      <div class="book-detail__layout">
        {_cover_img(book, css_class="book-cover book-cover--large")}
        <div class="book-detail__content">
          <header class="book-detail__header">
            <span class="badge badge--{esc(book.format)}">{esc(book.format.upper())}</span>
            <h1>{esc(title)}</h1>
{subtitle}            <p class="book-actions">
              <a class="button button--download" href="{esc(download_href(book.id))}">Download</a>
              <form class="book-actions__form book-actions__form--fetch" method="post" action="{esc(fetch_metadata_action())}" onsubmit="var b=this.querySelector('button');b.disabled=true;b.textContent='Fetching…';">
                <input type="hidden" name="id" value="{book.id}">
                <button type="submit" class="button button--fetch">Fetch metadata online</button>
              </form>
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
    return page_shell(title, body)


def render_error(message: str, *, status_hint: str = "Error") -> str:
    body = f"""    <div class="empty-state">
      <h2>{esc(status_hint)}</h2>
      <p>{esc(message)}</p>
      <p><a href="{esc(cgi_script('index.py'))}">Return to library</a></p>
    </div>
"""
    return page_shell(status_hint, body)
