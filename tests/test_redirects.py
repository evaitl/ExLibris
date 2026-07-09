"""Tests for post-login redirect validation."""

from __future__ import annotations

from exlibris.cgi.redirects import safe_post_login_redirect


def test_safe_post_login_redirect_accepts_index_and_book() -> None:
    assert safe_post_login_redirect("index.py") == "index.py"
    assert safe_post_login_redirect("book.py?id=42") == "book.py?id=42"
    assert (
        safe_post_login_redirect("book.py?id=42&sort=author&page=2")
        == "book.py?id=42&sort=author&page=2"
    )


def test_safe_post_login_redirect_rejects_unsafe_targets() -> None:
    assert safe_post_login_redirect("") == ""
    assert safe_post_login_redirect("book.py.evil") == ""
    assert safe_post_login_redirect("//evil.example/") == ""
    assert safe_post_login_redirect("https://evil.example/") == ""
    assert safe_post_login_redirect("book.py%0d%0aSet-Cookie:%20evil=1") == ""
    assert safe_post_login_redirect("book.py?id=1\r\nLocation: http://evil") == ""
