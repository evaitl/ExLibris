#!/usr/bin/env python3
"""CGI entry point: log out and clear the session cookie."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from exlibris.auth import admin_mode_cookie_header, clear_session_cookie_header
from exlibris.cgi.render import render_error


def main() -> None:
    if os.environ.get("REQUEST_METHOD", "GET").upper() != "POST":
        print("Content-Type: text/html; charset=utf-8")
        print("Status: 405 Method Not Allowed")
        print()
        print(render_error("POST required.", status_hint="Method not allowed"))
        return

    print("Status: 303 See Other")
    print("Location: index.py")
    print(clear_session_cookie_header())
    print(admin_mode_cookie_header(enabled=False))
    print()


if __name__ == "__main__":
    main()
