#!/usr/bin/env python3
"""CGI entry point: view a single book's metadata."""

from __future__ import annotations

import cgi
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from exlibris.cgi.common import (
    connect,
    get_book,
    get_current_user,
    is_favorite,
    neighbor_book_ids,
    parse_library_browse_context,
)
from exlibris.cgi.render import render_book_detail, render_error


def main() -> None:
    form = cgi.FieldStorage()
    raw_id = form.getfirst("id")

    if not raw_id or not str(raw_id).isdigit():
        print("Content-Type: text/html; charset=utf-8")
        print("Status: 400 Bad Request")
        print()
        print(render_error("Missing or invalid book id.", status_hint="Bad request"))
        return

    book_id = int(raw_id)
    notice = unquote(form.getfirst("notice") or "")
    error = unquote(form.getfirst("error") or "")

    try:
        with connect() as conn:
            current_user = get_current_user(conn)
            favorites_only = (
                form.getfirst("favorites") == "1" and current_user is not None
            )
            browse_ctx = parse_library_browse_context(
                title=form.getfirst("title", "") or "",
                author=form.getfirst("author", "") or "",
                publisher=form.getfirst("publisher", "") or "",
                genre=form.getfirst("genre", "") or "",
                language=form.getfirst("language", "") or "",
                sort=form.getfirst("sort", "title") or "title",
                sort_dir=form.getfirst("sort_dir", "") or "",
                page_size=form.getfirst("page_size", "") or "",
                page=form.getfirst("page", "1") or "1",
                favorites_only=favorites_only,
            )
            book = get_book(conn, book_id)
            favorite = (
                is_favorite(conn, user_id=current_user.id, book_id=book_id)
                if current_user is not None
                else False
            )
            prev_book_id, next_book_id = neighbor_book_ids(
                conn,
                book_id,
                browse_ctx,
                user_id=current_user.id if current_user else None,
            )
        if book is None:
            print("Content-Type: text/html; charset=utf-8")
            print("Status: 404 Not Found")
            print()
            print(render_error("Book not found.", status_hint="Not found"))
            return

        print("Content-Type: text/html; charset=utf-8")
        print()
        print(
            render_book_detail(
                book,
                browse_ctx=browse_ctx,
                prev_book_id=prev_book_id,
                next_book_id=next_book_id,
                notice=notice,
                error=error,
                current_user=current_user,
                is_favorite=favorite,
            )
        )
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
