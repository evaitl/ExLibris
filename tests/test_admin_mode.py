"""Tests for admin mode toggle."""

from __future__ import annotations

from exlibris.cgi.common import UserRow, admin_mode_enabled, is_admin, is_admin_user


def test_is_admin_user_checks_username(monkeypatch) -> None:
    monkeypatch.setattr(
        "exlibris.cgi.common.is_admin_username",
        lambda username: username == "alice",
    )
    assert is_admin_user(UserRow(id=1, username="alice"))
    assert not is_admin_user(UserRow(id=2, username="bob"))
    assert not is_admin_user(None)


def test_is_admin_requires_enabled_cookie(monkeypatch) -> None:
    monkeypatch.setattr(
        "exlibris.cgi.common.is_admin_username",
        lambda username: username == "alice",
    )
    user = UserRow(id=1, username="alice")
    monkeypatch.setenv("HTTP_COOKIE", "")
    assert not is_admin(user)
    monkeypatch.setenv("HTTP_COOKIE", "exlibris_admin_mode=1")
    assert is_admin(user)
    assert admin_mode_enabled()
