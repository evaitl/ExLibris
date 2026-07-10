"""Convert HTML book descriptions to plain text."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

_BLOCK_TAGS = frozenset(
    {"p", "div", "br", "hr", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}
)
_SKIP_TAGS = frozenset({"script", "style"})
_HTML_MARKUP = re.compile(r"<[a-zA-Z/!]")
_WHITESPACE = re.compile(r"\s+")


class _HTMLToTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        lowered = tag.lower()
        if lowered in _SKIP_TAGS:
            self._skip_depth += 1
        elif self._skip_depth == 0 and lowered in _BLOCK_TAGS:
            self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif self._skip_depth == 0 and lowered in _BLOCK_TAGS:
            self._parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        return _WHITESPACE.sub(" ", "".join(self._parts)).strip()


def _unescape_html(text: str, *, max_passes: int = 3) -> str:
    current = text
    for _ in range(max_passes):
        unescaped = html.unescape(current)
        if unescaped == current:
            break
        current = unescaped
    return current


def plain_text_description(description: str | None) -> str | None:
    """Strip HTML tags and decode HTML entities in a book description."""
    if description is None:
        return None
    text = description.strip()
    if not text:
        return None

    text = _unescape_html(text)
    if _HTML_MARKUP.search(text):
        parser = _HTMLToTextParser()
        parser.feed(text)
        parser.close()
        text = parser.text()

    text = _WHITESPACE.sub(" ", text).strip()
    return text or None


def description_needs_plaintext(description: str | None) -> bool:
    """True when plain_text_description would change the stored value."""
    if description is None:
        return False
    cleaned = plain_text_description(description)
    if cleaned is None:
        return bool(description.strip())
    return cleaned != description
