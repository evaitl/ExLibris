#!/usr/bin/env python3
"""CGI entry point: browse the indexed book collection."""

from __future__ import annotations

import cgi
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from exlibris.cgi.common import connect, list_books
from exlibris.cgi.render import render_error, render_library


def main() -> None:
    form = cgi.FieldStorage()
    title = form.getfirst("title", "") or ""
    author = form.getfirst("author", "") or ""
    publisher = form.getfirst("publisher", "") or ""
    genre = form.getfirst("genre", "") or ""
    language = form.getfirst("language", "") or ""
    sort = form.getfirst("sort", "title") or "title"

    try:
        with connect() as conn:
            books, total, options = list_books(
                conn,
                title=title,
                author=author,
                publisher=publisher,
                genre=genre,
                language=language,
                sort=sort,
            )
        html = render_library(
            books,
            total,
            options,
            selected_title=title,
            selected_author=author,
            selected_publisher=publisher,
            selected_genre=genre,
            selected_language=language,
            sort=sort,
        )
        print("Content-Type: text/html; charset=utf-8")
        print()
        print(html)
    except FileNotFoundError as exc:
        print("Content-Type: text/html; charset=utf-8")
        print("Status: 503 Service Unavailable")
        print()
        print(render_error(str(exc), status_hint="Database unavailable"))
    except Exception as exc:
        print("Content-Type: text/html; charset=utf-8")
        print("Status: 500 Internal Server Error")
        print()
        print(render_error(str(exc)))


if __name__ == "__main__":
    main()
