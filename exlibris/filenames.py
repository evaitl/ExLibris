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
        candidate = directory / f"{stem}.{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise OSError(f"could not find unused filename in {directory}")


def ensure_safe_filename(file_path: Path) -> Path:
    """Rename file_path on disk when its basename contains unsafe characters."""
    file_path = file_path.resolve()
    sanitized = sanitize_filename(file_path.name)
    if sanitized == file_path.name:
        return file_path
    target = unique_target_path(file_path.parent, sanitized)
    file_path.rename(target)
    return target.resolve()
