from __future__ import annotations

import re

from exlibris.author_tokens import tokenize_authors

_FTS_SPECIAL = re.compile(r'["*]')


def search_words(text: str) -> list[str]:
    return [word for word in text.split() if word]


def escape_fts_term(word: str) -> str:
    cleaned = _FTS_SPECIAL.sub(" ", word).strip()
    return cleaned.replace('"', '""')


def _like_prefix(token: str) -> str:
    escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"{escaped}%"


def append_author_token_filters(
    where: list[str],
    params: list[object],
    author: str,
) -> None:
    """Require every search word to match author tokens by prefix (indexed lookup)."""
    for word in search_words(author):
        for token in tokenize_authors(word):
            where.append(
                "books.id IN ("
                "SELECT book_id FROM book_author_tokens "
                "WHERE token LIKE ? ESCAPE '\\'"
                ")"
            )
            params.append(_like_prefix(token))


def fts_field_match(columns: list[str], words: list[str]) -> str | None:
    """Build an FTS5 column-group clause; every word must match (prefix search)."""
    terms: list[str] = []
    for word in words:
        term = escape_fts_term(word)
        if not term:
            continue
        terms.append(f'"{term}"*')
    if not terms:
        return None
    col_group = " ".join(columns)
    return f"{{{col_group}}} : ({' AND '.join(terms)})"


def build_fts_match(
    *,
    title: str = "",
    author: str = "",
    publisher: str = "",
    genre: str = "",
) -> str | None:
    """Combine per-field FTS clauses with AND (mirrors separate filter fields in the UI)."""
    parts: list[str] = []
    if title.strip():
        clause = fts_field_match(
            ["title", "sort_title", "file_name"],
            search_words(title),
        )
        if clause:
            parts.append(clause)
    if publisher.strip():
        clause = fts_field_match(["publisher"], search_words(publisher))
        if clause:
            parts.append(clause)
    if genre.strip():
        clause = fts_field_match(["tags"], search_words(genre))
        if clause:
            parts.append(clause)
    if not parts:
        return None
    return " AND ".join(parts)
