#!/usr/bin/env python3
"""CGI entry point: toggle admin mode for administrators."""

from __future__ import annotations

import cgi
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from exlibris.auth import admin_mode_cookie_header
from exlibris.cgi.common import cgi_script, connect, get_current_user, is_admin_user
from exlibris.cgi.render import render_error


def _redirect_back() -> str:
    referer = os.environ.get("HTTP_REFERER", "")
    if referer:
        parsed = urlparse(referer)
        if parsed.path:
            location = parsed.path
            if parsed.query:
                location += f"?{parsed.query}"
            return location
    return cgi_script("index.py")


def main() -> None:
    if os.environ.get("REQUEST_METHOD", "GET").upper() != "POST":
        print("Content-Type: text/html; charset=utf-8")
        print("Status: 405 Method Not Allowed")
        print()
        print(render_error("POST required.", status_hint="Method not allowed"))
        return

    with connect() as conn:
        current_user = get_current_user(conn)

    if not is_admin_user(current_user):
        print("Content-Type: text/html; charset=utf-8")
        print("Status: 403 Forbidden")
        print()
        print(render_error("Admin mode is not available.", status_hint="Forbidden"))
        return

    form = cgi.FieldStorage()
    enabled = form.getfirst("enabled") == "1"

    print("Status: 303 See Other")
    print(f"Location: {_redirect_back()}")
    print(admin_mode_cookie_header(enabled=enabled))
    print()


if __name__ == "__main__":
    main()
