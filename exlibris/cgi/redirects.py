"""Safe post-login redirect targets for CGI scripts."""

from __future__ import annotations

import re

# index.py or book.py?id=<digits> with optional browse query parameters.
_SAFE_NEXT_URL = re.compile(
    r"^(?:index\.py|book\.py\?id=\d+)"
    r"(?:&[a-z_]+=[^&#\r\n]*)*$"
)


def safe_post_login_redirect(url: str) -> str:
    """Return url when it is a safe relative CGI redirect, else empty string."""
    if not url:
        return ""
    if "\r" in url or "\n" in url or "\\" in url:
        return ""
    if url.startswith("/") or "://" in url:
        return ""
    if _SAFE_NEXT_URL.fullmatch(url):
        return url
    return ""
