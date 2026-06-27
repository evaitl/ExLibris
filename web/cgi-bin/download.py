#!/usr/bin/env python3
"""CGI entry point: download an ebook file."""

from __future__ import annotations

import cgi
import mimetypes
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from exlibris.cgi.common import allowed_book_file, connect


def main() -> None:
    form = cgi.FieldStorage()
    raw_id = form.getfirst("id")

    if not raw_id or not str(raw_id).isdigit():
        print("Status: 400 Bad Request")
        print("Content-Type: text/plain; charset=utf-8")
        print()
        print("Bad request")
        return

    book_id = int(raw_id)

    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT file_path, file_name, is_missing FROM books WHERE id = ?",
                (book_id,),
            ).fetchone()
    except FileNotFoundError:
        print("Status: 503 Service Unavailable")
        print("Content-Type: text/plain; charset=utf-8")
        print()
        print("Database unavailable")
        return

    if row is None or row["is_missing"]:
        print("Status: 404 Not Found")
        print("Content-Type: text/plain; charset=utf-8")
        print()
        print("Book not found")
        return

    book_file = allowed_book_file(row["file_path"])
    if book_file is None:
        print("Status: 404 Not Found")
        print("Content-Type: text/plain; charset=utf-8")
        print()
        print("Book file unavailable")
        return

    mime, _ = mimetypes.guess_type(str(book_file))
    filename = quote(row["file_name"])
    print(f"Content-Type: {mime or 'application/octet-stream'}")
    print(f'Content-Disposition: attachment; filename="{filename}"')
    print(f"Content-Length: {book_file.stat().st_size}")
    print()
    sys.stdout.flush()
    sys.stdout.buffer.write(book_file.read_bytes())


if __name__ == "__main__":
    main()
