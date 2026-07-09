#!/usr/bin/env python3
"""CGI entry point: log in with username and password."""

from __future__ import annotations

import cgi
import os
import sys
import traceback
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from exlibris.auth import session_cookie_header
from exlibris.cgi.redirects import safe_post_login_redirect
from exlibris.cgi.common import authenticate_user, connect, create_session_for_user
from exlibris.cgi.render import render_error, render_login


def _html(body: str, *, extra_headers: list[str] | None = None) -> None:
    print("Content-Type: text/html; charset=utf-8")
    for header in extra_headers or []:
        print(header)
    print()
    print(body)


def main() -> None:
    form = cgi.FieldStorage()
    next_url = safe_post_login_redirect(unquote(form.getfirst("next") or ""))

    if os.environ.get("REQUEST_METHOD", "GET").upper() != "POST":
        _html(render_login(next_url=next_url))
        return

    username = (form.getfirst("username") or "").strip()
    password = form.getfirst("password") or ""
    error = ""

    if not username or not password:
        error = "Enter both username and password."
    else:
        try:
            with connect() as conn:
                user = authenticate_user(conn, username=username, password=password)
            if user is None:
                error = "Invalid username or password."
            else:
                token = create_session_for_user(user)
                location = next_url or "index.py"
                print("Status: 303 See Other")
                print(f"Location: {location}")
                print(session_cookie_header(token))
                print()
                return
        except FileNotFoundError as exc:
            _html(render_error(str(exc), status_hint="Database unavailable"))
            return
        except Exception:
            traceback.print_exc(file=sys.stderr)
            error = "Unexpected error while signing in."

    _html(render_login(next_url=next_url, error=error))


if __name__ == "__main__":
    main()
