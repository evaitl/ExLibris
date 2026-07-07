"""Filename sanitization and safe terminal display."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

DANGEROUS_ASCII = frozenset('<>:"|?*')
DANGEROUS_CODEPOINTS = frozenset({
    0x200B, 0x200C, 0x200D, 0xFEFF,  # zero-width space/joiner/BOM
    0x202A, 0x202B, 0x202C, 0x202D, 0x202E,  # bidi overrides
    0x2066, 0x2067, 0x2068, 0x2069,  # bidi isolates
})


def display_text(text: str) -> str:
    """Return text safe to print on UTF-8 terminals."""
    return text.encode("utf-8", "surrogateescape").decode("utf-8", "replace")


def display_path(path: Path) -> str:
    """Return a path string safe to print on UTF-8 terminals."""
    return display_text(str(path))


def display_name(path: Path) -> str:
    """Return a filename safe to print on UTF-8 terminals."""
    return display_text(path.name)


def _sanitize_stem(stem: str) -> str:
    stem = stem.encode("utf-8", "surrogateescape").decode("utf-8", "ignore")
    stem = unicodedata.normalize("NFC", stem)
    cleaned: list[str] = []
    for ch in stem:
        code = ord(ch)
        if 0xD800 <= code <= 0xDFFF:
            continue
        if unicodedata.category(ch)[0] == "C":
            continue
        if ch in "/\\\0" or ch in DANGEROUS_ASCII or code in DANGEROUS_CODEPOINTS:
            continue
        cleaned.append(ch)
    stem = re.sub(r"\s+", " ", "".join(cleaned)).strip(" .")
    return stem or "unnamed"


def sanitize_filename(name: str, *, max_stem_len: int | None = None) -> str:
    """Drop illegal Unicode and unsafe characters from a filename."""
    path = Path(name)
    suffix = path.suffix
    stem = _sanitize_stem(path.stem if suffix else name)
    if max_stem_len is not None and len(stem) > max_stem_len:
        stem = stem[:max_stem_len].rstrip() or "unnamed"
    return f"{stem}{suffix}" if suffix else stem


def filename_needs_sanitization(name: str) -> bool:
    """True when name contains characters that sanitize_filename would remove."""
    return sanitize_filename(name) != name


def unique_target_path(directory: Path, file_name: str) -> Path:
    """Return an unused path for file_name inside directory."""
    candidate = directory / file_name
    if not candidate.exists():
        return candidate
    stem = Path(file_name).stem
    suffix = Path(file_name).suffix
    for index in range(2, 10_000):
        candidate = directory / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
    raise OSError(f"could not find unused filename in {directory}")


def _clean_metadata_field(value: object | None, *, fallback: str) -> str:
    if value is None:
        return fallback
    text = re.sub(r"\s+", " ", str(value).strip())
    return text or fallback


def has_short_basename(file_name: str, *, max_stem_len: int = 10) -> bool:
    path = Path(file_name)
    if path.suffix.lower() != ".epub":
        return False
    return len(path.stem) < max_stem_len


def build_metadata_filename(
    *,
    title: str | None,
    authors: str | None,
    publisher: str | None,
    fallback_stem: str,
    max_stem_len: int | None = 180,
) -> str:
    """Build '{title} - {authors}-({publisher}).epub' with safe characters."""
    title_text = _clean_metadata_field(title, fallback=fallback_stem)
    authors_text = _clean_metadata_field(authors, fallback="Unknown Author")
    publisher_text = _clean_metadata_field(publisher, fallback="Unknown Publisher")
    raw = f"{title_text} - {authors_text}-({publisher_text}).epub"
    return sanitize_filename(raw, max_stem_len=max_stem_len)


def target_filename(
    file_name: str,
    *,
    title: str | None = None,
    authors: str | None = None,
    publisher: str | None = None,
    max_short_stem_len: int = 10,
    max_stem_len: int | None = 180,
) -> str:
    """Desired on-disk filename after sanitization and optional metadata rename."""
    path = Path(file_name)
    if path.suffix.lower() != ".epub":
        return sanitize_filename(file_name, max_stem_len=max_stem_len)

    if has_short_basename(file_name, max_stem_len=max_short_stem_len):
        candidate = build_metadata_filename(
            title=title,
            authors=authors,
            publisher=publisher,
            fallback_stem=path.stem,
            max_stem_len=max_stem_len,
        )
    else:
        candidate = file_name
    return sanitize_filename(candidate, max_stem_len=max_stem_len)


def filename_needs_update(
    file_name: str,
    *,
    title: str | None = None,
    authors: str | None = None,
    publisher: str | None = None,
    max_short_stem_len: int = 10,
) -> bool:
    return (
        target_filename(
            file_name,
            title=title,
            authors=authors,
            publisher=publisher,
            max_short_stem_len=max_short_stem_len,
        )
        != file_name
    )


def ensure_safe_filename(file_path: Path) -> Path:
    """Rename file_path on disk when its basename contains unsafe characters."""
    file_path = file_path.resolve()
    sanitized = sanitize_filename(file_path.name)
    if sanitized == file_path.name:
        return file_path
    target = unique_target_path(file_path.parent, sanitized)
    file_path.rename(target)
    return target.resolve()
