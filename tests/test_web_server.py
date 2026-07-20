"""Tests for the built-in CGI web server helpers."""

from __future__ import annotations

from exlibris.web_server import parse_cgi_response


def test_parse_cgi_response_honors_status_redirect() -> None:
    stdout = (
        b"Status: 303 See Other\r\n"
        b"Location: /cgi-bin/index.py\r\n"
        b"Set-Cookie: exlibris_admin_mode=1; Path=/\r\n"
        b"\r\n"
    )
    status, reason, headers, body = parse_cgi_response(stdout)
    assert status == 303
    assert reason == "See Other"
    assert ("Location", "/cgi-bin/index.py") in headers
    assert ("Set-Cookie", "exlibris_admin_mode=1; Path=/") in headers
    assert not any(name.lower() == "status" for name, _ in headers)
    assert body == b""


def test_parse_cgi_response_status_after_content_type() -> None:
    stdout = (
        b"Content-Type: text/html; charset=utf-8\n"
        b"Status: 403 Forbidden\n"
        b"\n"
        b"<html>nope</html>"
    )
    status, reason, headers, body = parse_cgi_response(stdout)
    assert status == 403
    assert reason == "Forbidden"
    assert ("Content-Type", "text/html; charset=utf-8") in headers
    assert body == b"<html>nope</html>"


def test_parse_cgi_response_default_ok() -> None:
    stdout = b"Content-Type: text/plain\n\nhello"
    status, reason, headers, body = parse_cgi_response(stdout)
    assert status == 200
    assert reason == "OK"
    assert body == b"hello"
