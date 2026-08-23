"""Closed genre vocabulary and comma-separated Genre field helpers.

Stdlib only — imported by CGI and the classifier CLI.
"""

from __future__ import annotations

EROTICA_LABEL = "Erotica"
FICTION_FALLBACK = "Fiction"
NONFICTION_FALLBACK = "Non-fiction"
MAX_GENRE_LABELS = 3

FICTION_GENRES: tuple[str, ...] = (
    "Literary Fiction",
    "Contemporary Fiction",
    "Historical Fiction",
    "Fantasy",
    "Science Fiction",
    "Mystery",
    "Thriller",
    "Crime",
    "Horror",
    "Romance",
    EROTICA_LABEL,
    "Adventure",
    "Western",
    "War Fiction",
    "Humor",
    "Paranormal",
    "Dystopian",
    "Magical Realism",
    "Young Adult",
    "Middle Grade",
    "Children's",
    "Short Stories",
    "Poetry",
    "Drama",
)

NONFICTION_GENRES: tuple[str, ...] = (
    "Biography",
    "Memoir",
    "History",
    "Military History",
    "Politics",
    "Philosophy",
    "Religion",
    "Spirituality",
    "Psychology",
    "Self-Help",
    "Business",
    "Economics",
    "Personal Finance",
    "Science",
    "Mathematics",
    "Technology",
    "Computers",
    "Medicine",
    "Health and Fitness",
    "Nature",
    "Environment",
    "Travel",
    "True Crime",
    "Education",
    "Reference",
    "Language",
    "Art",
    "Music",
    "Cooking",
    "Crafts and Hobbies",
    "Sports",
    "Parenting",
    "Law",
    "Social Science",
    "Essays",
    "Journalism",
)

ALL_GENRES: tuple[str, ...] = FICTION_GENRES + NONFICTION_GENRES
ALLOWED_GENRE_LABELS: frozenset[str] = frozenset(
    ALL_GENRES + (FICTION_FALLBACK, NONFICTION_FALLBACK)
)
FICTION_FAMILY: frozenset[str] = frozenset(FICTION_GENRES + (FICTION_FALLBACK,))
NONFICTION_FAMILY: frozenset[str] = frozenset(
    NONFICTION_GENRES + (NONFICTION_FALLBACK,)
)

_LOOKUP: dict[str, str] = {label.casefold(): label for label in ALLOWED_GENRE_LABELS}


def canonical_genre_label(value: str) -> str | None:
    cleaned = " ".join(value.split())
    if not cleaned:
        return None
    return _LOOKUP.get(cleaned.casefold())


def parse_genre_labels(raw: str | None) -> list[str]:
    """Split a stored or submitted Genre string into canonical labels."""
    if not raw or not raw.strip():
        return []
    labels: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        canonical = canonical_genre_label(part)
        if canonical is None:
            token = " ".join(part.split())
            if token:
                raise ValueError(f"Unknown genre: {token}")
            continue
        if canonical in seen:
            continue
        seen.add(canonical)
        labels.append(canonical)
        if len(labels) >= MAX_GENRE_LABELS:
            break
    return labels


def format_genre_labels(labels: list[str]) -> str | None:
    cleaned = [label for label in labels if label in ALLOWED_GENRE_LABELS]
    if not cleaned:
        return None
    unique: list[str] = []
    seen: set[str] = set()
    for label in cleaned:
        if label in seen:
            continue
        seen.add(label)
        unique.append(label)
        if len(unique) >= MAX_GENRE_LABELS:
            break
    return ", ".join(unique)


def genre_contains(genre: str | None, label: str) -> bool:
    if not genre or not label:
        return False
    try:
        tokens = parse_genre_labels(genre)
    except ValueError:
        tokens = [part.strip() for part in genre.split(",") if part.strip()]
    want = canonical_genre_label(label) or label
    return any(token.casefold() == want.casefold() for token in tokens)


def genre_contains_erotica(genre: str | None) -> bool:
    return genre_contains(genre, EROTICA_LABEL)


def padded_genre_sql(column: str = "books.genre") -> str:
    """SQL expression that wraps comma-separated labels as ,Label,Label, for token LIKE."""
    return f"(',' || REPLACE(COALESCE({column}, ''), ', ', ',') || ',')"


def genre_token_like_sql(column: str = "books.genre") -> str:
    return f"{padded_genre_sql(column)} LIKE '%,' || ? || ',%' COLLATE NOCASE"


def erotica_hidden_sql(column: str = "books.genre") -> str:
    """True when the row should be hidden from non-admins."""
    padded = padded_genre_sql(column)
    return f"({column} IS NOT NULL AND {padded} LIKE '%,Erotica,%' COLLATE NOCASE)"


def erotica_visible_sql(column: str = "books.genre") -> str:
    """True when the row may be shown to non-admins (no Erotica token)."""
    padded = padded_genre_sql(column)
    return f"({column} IS NULL OR {padded} NOT LIKE '%,Erotica,%' COLLATE NOCASE)"


def filter_genre_groups(*, include_erotica: bool) -> list[tuple[str, tuple[str, ...]]]:
    fiction = tuple(
        label
        for label in FICTION_GENRES + (FICTION_FALLBACK,)
        if include_erotica or label != EROTICA_LABEL
    )
    nonfiction = NONFICTION_GENRES + (NONFICTION_FALLBACK,)
    return [("Fiction", fiction), ("Non-fiction", nonfiction)]
