#!/usr/bin/env python3
"""CGI entry point: update book metadata (administrators only)."""

from __future__ import annotations

import cgi
import os
import sqlite3
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from exlibris.cgi.common import (
    EditBookError,
    UserRow,
    book_detail_context,
    book_edit_fields,
    connect,
    connect_rw,
    get_book,
    is_admin,
    parse_library_browse_context_from_form,
    parse_stored_neighbor_ids,
    update_book_fields,
)
from exlibris.cgi.render import render_book_detail, render_error


def _html(body: str) -> None:
    print("Content-Type: text/html; charset=utf-8")
    print()
    print(body)


def _detail_response(
    conn,
    book,
    form,
    *,
    notice: str = "",
    error: str = "",
    current_user: UserRow | None,
    is_favorite: bool,
    use_stored_neighbors: bool,
) -> str:
    browse_ctx = parse_library_browse_context_from_form(
        form, current_user=current_user, prefix="lib_"
    )
    if use_stored_neighbors:
        prev_book_id, next_book_id = parse_stored_neighbor_ids(form)
    else:
        prev_book_id, next_book_id = neighbor_book_ids(
            conn,
            book.id,
            browse_ctx,
            user_id=current_user.id if current_user else None,
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
    title = form.getfirst("title")
    authors = form.getfirst("authors")
    genre = form.getfirst("genre")

    with connect() as conn:
        current_user, is_favorite = book_detail_context(conn, book_id)
        book = get_book(conn, book_id)

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
                    error="Only administrators can edit book metadata.",
                    current_user=current_user,
                    is_favorite=is_favorite,
                    use_stored_neighbors=True,
                )
            )
        return

    try:
        fields = book_edit_fields(title=title, authors=authors, genre=genre)
    except EditBookError as exc:
        with connect() as conn:
            _html(
                _detail_response(
                    conn,
                    book,
                    form,
                    error=str(exc),
                    current_user=current_user,
                    is_favorite=is_favorite,
                    use_stored_neighbors=True,
                )
            )
        return

    try:
        with connect_rw() as conn:
            book = get_book(conn, book_id)
            if book is None:
                _html(render_error("Book not found.", status_hint="Not found"))
                return
            update_book_fields(conn, book_id, fields)

        with connect() as conn:
            book = get_book(conn, book_id)
            current_user, is_favorite = book_detail_context(conn, book_id)
        if book is None:
            _html(render_error("Book not found after update.", status_hint="Not found"))
            return

        with connect() as conn:
            _html(
                _detail_response(
                    conn,
                    book,
                    form,
                    notice="Book metadata updated",
                    current_user=current_user,
                    is_favorite=is_favorite,
                    use_stored_neighbors=True,
                )
            )
    except FileNotFoundError as exc:
        _html(render_error(str(exc), status_hint="Database unavailable"))
    except PermissionError as exc:
        with connect() as conn:
            book = get_book(conn, book_id)
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
                    use_stored_neighbors=True,
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
                    use_stored_neighbors=True,
                )
            )
    except Exception:
        traceback.print_exc(file=sys.stderr)
        with connect() as conn:
            book = get_book(conn, book_id)
            current_user, is_favorite = book_detail_context(conn, book_id)
        if book is None:
            _html(render_error("Unexpected error while updating book."))
            return
        with connect() as conn:
            _html(
                _detail_response(
                    conn,
                    book,
                    form,
                    error="Unexpected error while updating book.",
                    current_user=current_user,
                    is_favorite=is_favorite,
                    use_stored_neighbors=True,
                )
            )


if __name__ == "__main__":
    main()
