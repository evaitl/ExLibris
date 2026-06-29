from __future__ import annotations

from pathlib import Path

from exlibris.config import PROJECT_ROOT

_ADMINS_FILE = PROJECT_ROOT / "admins.txt"


def admins_file_path() -> Path:
    return _ADMINS_FILE


def load_admin_usernames() -> frozenset[str]:
    """Usernames listed in admins.txt (case-insensitive)."""
    path = admins_file_path()
    if not path.is_file():
        return frozenset()
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        names.add(line.casefold())
    return frozenset(names)


def is_admin_username(username: str | None) -> bool:
    if not username:
        return False
    return username.casefold() in load_admin_usernames()
