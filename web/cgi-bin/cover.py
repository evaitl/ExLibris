#!/usr/bin/env python3
"""CGI entry point: serve a book cover image."""

from __future__ import annotations

import cgi
import mimetypes
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from exlibris.cgi.common import connect, project_root


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
                "SELECT cover_path FROM books WHERE id = ?",
                (book_id,),
            ).fetchone()
    except FileNotFoundError:
        print("Status: 503 Service Unavailable")
        print("Content-Type: text/plain; charset=utf-8")
        print()
        print("Database unavailable")
        return

    if row is None or not row["cover_path"]:
        print("Status: 404 Not Found")
        print("Content-Type: text/plain; charset=utf-8")
        print()
        print("Cover not found")
        return

    cover_path = project_root() / row["cover_path"]
    if not cover_path.is_file():
        print("Status: 404 Not Found")
        print("Content-Type: text/plain; charset=utf-8")
        print()
        print("Cover file missing")
        return

    mime, _ = mimetypes.guess_type(str(cover_path))
    print(f"Content-Type: {mime or 'application/octet-stream'}")
    print("Cache-Control: public, max-age=86400")
    print()
    sys.stdout.flush()
    sys.stdout.buffer.write(cover_path.read_bytes())


if __name__ == "__main__":
    main()
