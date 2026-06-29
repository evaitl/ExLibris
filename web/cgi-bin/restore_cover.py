#!/usr/bin/env python3
"""CGI entry point: restore a book cover from its embedded ebook image."""

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
    UserRow,
    allowed_book_file,
    book_detail_context,
    book_detail_navigation_from_form,
    connect,
    connect_rw,
    get_book,
    update_book_fields,
)
from exlibris.cgi.render import render_book_detail, render_error
from exlibris.fetch_metadata import FetchMetadataError, restore_embedded_cover


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

    try:
        with connect_rw() as conn:
            book = get_book(conn, book_id)
            if book is None or book.is_missing:
                _html(render_error("Book not found.", status_hint="Not found"))
                return

            book_file = allowed_book_file(book.file_path)
            if book_file is None:
                with connect() as read_conn:
                    current_user, is_favorite = book_detail_context(read_conn, book_id)
                _html(
                    _detail_response(
                        read_conn,
                        book,
                        form,
                        error="Book file is not available for cover restore",
                        current_user=current_user,
                        is_favorite=is_favorite,
                    )
                )
                return

            cover_path = restore_embedded_cover(
                ebook_path=book_file,
                book_id=book_id,
            )
            update_book_fields(conn, book_id, {"cover_path": cover_path})

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
                    notice="Cover restored from ebook file",
                    current_user=current_user,
                    is_favorite=is_favorite,
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
                )
            )
    except FetchMetadataError as exc:
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
                )
            )
    except Exception:
        detail = traceback.format_exc()
        sys.stderr.write(detail)
        with connect() as conn:
            book = get_book(conn, book_id)
            current_user, is_favorite = book_detail_context(conn, book_id)
        if book is None:
            _html(render_error("Unexpected error while restoring cover."))
            return
        with connect() as conn:
            _html(
                _detail_response(
                    conn,
                    book,
                    form,
                    error="Unexpected error while restoring cover.",
                    current_user=current_user,
                    is_favorite=is_favorite,
                )
            )


if __name__ == "__main__":
    main()
