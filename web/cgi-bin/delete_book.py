#!/usr/bin/env python3
"""CGI entry point: delete a book file and database row (administrators only)."""

from __future__ import annotations

import cgi
import os
import sqlite3
import sys
import traceback
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from exlibris.cgi.common import (
    DeleteBookError,
    UserRow,
    book_detail_context,
    book_detail_navigation_from_form,
    cgi_script,
    connect,
    connect_rw,
    delete_book,
    get_book,
    is_admin,
)
from exlibris.cgi.render import render_book_detail, render_error


def _html(body: str) -> None:
    print("Content-Type: text/html; charset=utf-8")
    print()
    print(body)


def _redirect(url: str) -> None:
    print("Status: 303 See Other")
    print("Content-Type: text/html; charset=utf-8")
    print(f"Location: {url}")
    print()


def _detail_response(
    conn,
    book,
    form,
    *,
    notice: str = "",
    error: str = "",
    current_user: UserRow | None,
    is_favorite: bool,
) -> str:
    browse_ctx, prev_book_id, next_book_id = book_detail_navigation_from_form(
        conn,
        book.id,
        form,
        current_user=current_user,
        use_stored_neighbors=True,
    )
    return render_book_detail(
        book,
        browse_ctx=browse_ctx,
        prev_book_id=prev_book_id,
        next_book_id=next_book_id,
        notice=notice,
        error=error,
        current_user=current_user,
        is_favorite=is_favorite,
    )


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

    with connect() as conn:
        current_user, is_favorite = book_detail_context(conn, book_id)
        book = get_book(conn, book_id, include_missing=True)

    if book is None:
        _html(render_error("Book not found.", status_hint="Not found"))
        return

    if not is_admin(current_user):
        with connect() as conn:
            _html(
                _detail_response(
                    conn,
                    book,
                    form,
                    error="Only administrators can delete books.",
                    current_user=current_user,
                    is_favorite=is_favorite,
                )
            )
        return

    title = book.title or book.file_name

    try:
        with connect() as conn:
            browse_ctx, prev_book_id, next_book_id = book_detail_navigation_from_form(
                conn,
                book_id,
                form,
                current_user=current_user,
                use_stored_neighbors=True,
            )

        with connect_rw() as conn:
            book = get_book(conn, book_id, include_missing=True)
            if book is None:
                _html(render_error("Book not found.", status_hint="Not found"))
                return
            delete_book(conn, book)

        notice = f'"{title}" deleted'
        target_id = next_book_id or prev_book_id
        if target_id is not None:
            with connect() as conn:
                if get_book(conn, target_id) is None:
                    target_id = None

        if target_id is not None:
            params = browse_ctx.normalized().query_params(book_id=target_id)
            params["notice"] = notice
            redirect_url = f"{cgi_script('book.py')}?{urlencode(params)}"
        else:
            params = browse_ctx.normalized().query_params()
            params["notice"] = notice
            redirect_url = f"{cgi_script('index.py')}?{urlencode(params)}"
        _redirect(redirect_url)
    except FileNotFoundError as exc:
        _html(render_error(str(exc), status_hint="Database unavailable"))
    except PermissionError as exc:
        with connect() as conn:
            book = get_book(conn, book_id, include_missing=True)
            current_user, is_favorite = book_detail_context(conn, book_id)
        if book is None:
            _html(render_error(str(exc)))
            return
        with connect() as conn:
            _html(
                _detail_response(
                    conn,
                    book,
                    form,
                    error=str(exc),
                    current_user=current_user,
                    is_favorite=is_favorite,
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
            book = get_book(conn, book_id, include_missing=True)
            current_user, is_favorite = book_detail_context(conn, book_id)
        if book is None:
            _html(render_error(message))
            return
        with connect() as conn:
            _html(
                _detail_response(
                    conn,
                    book,
                    form,
                    error=message,
                    current_user=current_user,
                    is_favorite=is_favorite,
                )
            )
    except DeleteBookError as exc:
        with connect() as conn:
            book = get_book(conn, book_id, include_missing=True)
            current_user, is_favorite = book_detail_context(conn, book_id)
        if book is None:
            _html(render_error(str(exc)))
            return
        with connect() as conn:
            _html(
                _detail_response(
                    conn,
                    book,
                    form,
                    error=str(exc),
                    current_user=current_user,
                    is_favorite=is_favorite,
                )
            )
    except Exception:
        traceback.print_exc(file=sys.stderr)
        with connect() as conn:
            book = get_book(conn, book_id, include_missing=True)
            current_user, is_favorite = book_detail_context(conn, book_id)
        if book is None:
            _html(render_error("Unexpected error while deleting book."))
            return
        with connect() as conn:
            _html(
                _detail_response(
                    conn,
                    book,
                    form,
                    error="Unexpected error while deleting book.",
                    current_user=current_user,
                    is_favorite=is_favorite,
                )
            )


if __name__ == "__main__":
    main()
