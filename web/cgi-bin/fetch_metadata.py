#!/usr/bin/env python3
"""CGI entry point: fetch online metadata for a book and update the database."""

from __future__ import annotations

import cgi
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from exlibris.cgi.common import (
    allowed_book_file,
    connect,
    connect_rw,
    get_book,
    update_book_fields,
)
from exlibris.cgi.render import render_book_detail, render_error
from exlibris.fetch_metadata import FetchMetadataError, enrich_book_from_online


def _html(body: str) -> None:
    print("Content-Type: text/html; charset=utf-8")
    print()
    print(body)


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
                _html(
                    render_book_detail(
                        book,
                        error="Book file is not available for update",
                    )
                )
                return

            fields = enrich_book_from_online(
                book_file,
                title=book.title,
                authors=book.authors,
                isbn=book.isbn,
                book_id=book_id,
            )
            update_book_fields(conn, book_id, fields.fields)

        with connect() as conn:
            book = get_book(conn, book_id)
        if book is None:
            _html(render_error("Book not found after update.", status_hint="Not found"))
            return

        notice = "Metadata updated from online sources"
        if fields.cover_updated:
            notice += " and cover image"
        _html(render_book_detail(book, notice=notice))
    except FileNotFoundError as exc:
        _html(render_error(str(exc), status_hint="Database unavailable"))
    except FetchMetadataError as exc:
        with connect() as conn:
            book = get_book(conn, book_id)
        if book is None:
            _html(render_error(str(exc)))
            return
        _html(render_book_detail(book, error=str(exc)))
    except Exception:
        detail = traceback.format_exc()
        sys.stderr.write(detail)
        with connect() as conn:
            book = get_book(conn, book_id)
        if book is None:
            _html(render_error("Unexpected error while fetching metadata."))
            return
        _html(render_book_detail(book, error="Unexpected error while fetching metadata."))


if __name__ == "__main__":
    main()
