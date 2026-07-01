#!/usr/bin/env python3
"""CGI entry point: browse the indexed book collection."""

from __future__ import annotations

import cgi
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from exlibris.cgi.common import connect, favorite_book_ids, get_current_user, list_books
from exlibris.cgi.render import render_error, render_library


def main() -> None:
    form = cgi.FieldStorage()
    title = form.getfirst("title", "") or ""
    author = form.getfirst("author", "") or ""
    publisher = form.getfirst("publisher", "") or ""
    genre = form.getfirst("genre", "") or ""
    language = form.getfirst("language", "") or ""
    sort = form.getfirst("sort", "title") or "title"
    sort_dir = form.getfirst("sort_dir", "") or ""
    raw_page_size = form.getfirst("page_size", "") or ""
    raw_page = form.getfirst("page", "1") or "1"
    page = int(raw_page) if str(raw_page).isdigit() else 1

    try:
        with connect() as conn:
            current_user = get_current_user(conn)
            favorites_only = (
                form.getfirst("favorites") == "1" and current_user is not None
            )
            books, filtered_count, library_total, page, options = list_books(
                conn,
                title=title,
                author=author,
                publisher=publisher,
                genre=genre,
                language=language,
                sort=sort,
                sort_dir=sort_dir,
                page=page,
                page_size=raw_page_size,
                favorites_only=favorites_only,
                user_id=current_user.id if current_user else None,
            )
            fav_ids = (
                favorite_book_ids(conn, current_user.id)
                if current_user is not None
                else frozenset()
            )
        html = render_library(
            books,
            filtered_count,
            library_total,
            page,
            options,
            selected_title=title,
            selected_author=author,
            selected_publisher=publisher,
            selected_genre=genre,
            selected_language=language,
            sort=sort,
            sort_dir=sort_dir,
            page_size=raw_page_size,
            favorites_only=favorites_only,
            current_user=current_user,
            favorite_book_ids=fav_ids,
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
