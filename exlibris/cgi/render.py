from __future__ import annotations

from exlibris.cgi.common import (
    BookRow,
    FilterOptions,
    cgi_script,
    cover_href,
    download_href,
    esc,
    fetch_metadata_action,
    format_size,
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


def render_library(
    books: list[BookRow],
    total: int,
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
        for key in ("title", "author", "published", "size", "scanned")
    }

    if books:
        cards = "\n".join(_book_card(book) for book in books)
        collection = f"""    <ul class="book-grid">
{cards}
    </ul>"""
    else:
        collection = """    <div class="empty-state">
      <h2>No books found</h2>
      <p>Try clearing filters or run <code>python scan_books.py</code> to index your library.</p>
    </div>"""

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
          <label class="filter-field filter-field--publisher">
            <span class="filter-field__label">Publisher</span>
            <select name="publisher" aria-label="Filter by publisher">
{_select_options(options.publishers, selected_publisher, "All publishers", max_label=48)}
            </select>
          </label>
          <label class="filter-field filter-field--genre">
            <span class="filter-field__label">Genre</span>
            <select name="genre" aria-label="Filter by genre">
{_select_options(options.genres, selected_genre, "All genres", max_label=48)}
            </select>
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
            </select>
          </label>
          <div class="filter-form__buttons">
            <button type="submit">Apply filters</button>
            <a class="filter-clear" href="{esc(cgi_script('index.py'))}">Clear</a>
          </div>
        </div>
      </form>
      <p class="stats">{len(books)} shown · {total} total in library</p>
    </section>

{collection}
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
        ("Published", book.published_date),
        ("ISBN", book.isbn),
        ("Language", book.language),
        ("Genre", book.tags),
        ("Pages", str(book.page_count) if book.page_count is not None else None),
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
