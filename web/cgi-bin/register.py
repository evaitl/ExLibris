#!/usr/bin/env python3
"""CGI entry point: create a new user account."""

from __future__ import annotations

import cgi
import os
import sqlite3
import sys
import traceback
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from exlibris.auth import session_cookie_header
from exlibris.cgi.common import (
    UserRow,
    connect_rw,
    create_session_for_user,
)
from exlibris.cgi.render import render_error, render_register
from exlibris.users import UserError, register_user


def _html(body: str, *, extra_headers: list[str] | None = None) -> None:
    print("Content-Type: text/html; charset=utf-8")
    for header in extra_headers or []:
        print(header)
    print()
    print(body)


def _valid_next_url(next_url: str) -> str:
    if next_url.startswith("index.py") or next_url.startswith("book.py"):
        return next_url
    return ""


def main() -> None:
    form = cgi.FieldStorage()
    next_url = _valid_next_url(unquote(form.getfirst("next") or ""))

    if os.environ.get("REQUEST_METHOD", "GET").upper() != "POST":
        _html(render_register(next_url=next_url))
        return

    username = (form.getfirst("username") or "").strip()
    password = form.getfirst("password") or ""
    password_confirm = form.getfirst("password_confirm") or ""
    error = ""

    try:
        with connect_rw() as conn:
            user_id, cleaned = register_user(
                conn,
                username=username,
                password=password,
                password_confirm=password_confirm,
            )
        user = UserRow(id=user_id, username=cleaned)
        token = create_session_for_user(user)
        location = next_url or "index.py"
        print("Status: 303 See Other")
        print(f"Location: {location}")
        print(session_cookie_header(token))
        print()
    except UserError as exc:
        error = str(exc)
        _html(
            render_register(
                next_url=next_url,
                error=error,
                username=username,
            )
        )
    except FileNotFoundError as exc:
        _html(render_error(str(exc), status_hint="Database unavailable"))
    except PermissionError as exc:
        _html(render_error(str(exc), status_hint="Permission denied"))
    except sqlite3.OperationalError as exc:
        message = str(exc)
        if "no such table: users" in message.lower():
            message = (
                "User accounts are not set up yet. Run "
                "'exlibris user create' or 'exlibris scan' once on the server "
                "to apply database migrations."
            )
        elif "readonly" in message.lower():
            message = (
                "Database is read-only for the web server. Grant www-data write "
                "access to the data/ directory (see scripts/setup-data-dir.sh)."
            )
        _html(render_error(message, status_hint="Cannot create account"))
    except Exception:
        traceback.print_exc(file=sys.stderr)
        _html(
            render_register(
                next_url=next_url,
                error="Unexpected error while creating account.",
                username=username,
            )
        )


if __name__ == "__main__":
    main()
