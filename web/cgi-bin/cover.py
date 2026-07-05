#!/usr/bin/env python3
"""Redirect legacy cover.cgi requests to static cover URLs."""

from __future__ import annotations

import cgi
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from exlibris.cgi.common import connect, cover_href, project_root


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
    version = form.getfirst("v") or ""

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

    cover_path = str(row["cover_path"])
    if not (project_root() / cover_path).is_file():
        print("Status: 404 Not Found")
        print("Content-Type: text/plain; charset=utf-8")
        print()
        print("Cover file missing")
        return

    url = cover_href(cover_path, version=unquote(version) or None)
    if not url:
        print("Status: 404 Not Found")
        print("Content-Type: text/plain; charset=utf-8")
        print()
        print("Cover not found")
        return

    print("Status: 301 Moved Permanently")
    print(f"Location: {url}")
    print("Content-Type: text/html; charset=utf-8")
    print()


if __name__ == "__main__":
    main()
