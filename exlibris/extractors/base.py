from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class BookMetadata:
    title: str | None = None
    authors: str | None = None
    publisher: str | None = None
    published_date: str | None = None
    isbn: str | None = None
    language: str | None = None
    description: str | None = None
    format: str = ""
    errors: list[str] = field(default_factory=list)


def _join_authors(values: list[str]) -> str | None:
    cleaned = [v.strip() for v in values if v and v.strip()]
    return "; ".join(cleaned) if cleaned else None


def _first_str(*values) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None
