#!/usr/bin/env python3
"""CGI entry point: add or remove a book from the current user's favorites."""

from __future__ import annotations

import cgi
import os
import sqlite3
import sys
import traceback
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from exlibris.cgi.common import (
    connect,
    connect_rw,
    get_book,
    get_current_user,
    set_favorite,
)
from exlibris.cgi.render import render_book_detail, render_error


def _html(body: str) -> None:
    print("Content-Type: text/html; charset=utf-8")
    print()
    print(body)


def _redirect_to_login(book_id: int) -> None:
    next_url = quote(f"book.py?id={book_id}", safe="")
    print("Status: 303 See Other")
    print(f"Location: login.py?next={next_url}")
    print()


def main() -> None:
    if os.environ.get("REQUEST_METHOD", "GET").upper() != "POST":
        _html(render_error("POST required.", status_hint="Method not allowed"))
        return

    form = cgi.FieldStorage()
    raw_id = form.getfirst("id")
    if not raw_id or not str(raw_id).isdigit():
        _html(render_error("Missing or invalid book id.", status_hint="Bad request"))
        return

    book_id = int(raw_id)
    favorite_values = form.getlist("favorite")
    favorite = favorite_values[-1] == "1" if favorite_values else False

    try:
        with connect() as conn:
            user = get_current_user(conn)
            book = get_book(conn, book_id)
        if user is None:
            _redirect_to_login(book_id)
            return
        if book is None:
            _html(render_error("Book not found.", status_hint="Not found"))
            return

        with connect_rw() as conn:
            set_favorite(conn, user_id=user.id, book_id=book_id, favorite=favorite)

        with connect() as conn:
            book = get_book(conn, book_id)
            user = get_current_user(conn)
        if book is None or user is None:
            _html(render_error("Book not found after update.", status_hint="Not found"))
            return

        notice = "Added to favorites" if favorite else "Removed from favorites"
        _html(
            render_book_detail(
                book,
                current_user=user,
                is_favorite=favorite,
                notice=notice,
            )
        )
    except FileNotFoundError as exc:
        _html(render_error(str(exc), status_hint="Database unavailable"))
    except PermissionError as exc:
        with connect() as conn:
            book = get_book(conn, book_id)
            user = get_current_user(conn)
        if book is None:
            _html(render_error(str(exc)))
            return
        _html(
            render_book_detail(
                book,
                current_user=user,
                is_favorite=favorite,
                error=str(exc),
            )
        )
    except sqlite3.OperationalError as exc:
        message = str(exc)
        if "readonly" in message.lower():
            message = (
                "Database is read-only for the web server. Grant www-data write "
                "access to the data/ directory (see scripts/setup-data-dir.sh)."
            )
        with connect() as conn:
            book = get_book(conn, book_id)
            user = get_current_user(conn)
        if book is None:
            _html(render_error(message))
            return
        _html(
            render_book_detail(
                book,
                current_user=user,
                is_favorite=favorite,
                error=message,
            )
        )
    except Exception:
        traceback.print_exc(file=sys.stderr)
        with connect() as conn:
            book = get_book(conn, book_id)
            user = get_current_user(conn)
        if book is None:
            _html(render_error("Unexpected error while updating favorites."))
            return
        _html(
            render_book_detail(
                book,
                current_user=user,
                is_favorite=favorite,
                error="Unexpected error while updating favorites.",
            )
        )


if __name__ == "__main__":
    main()
