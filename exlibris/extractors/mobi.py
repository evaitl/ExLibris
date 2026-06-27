from pathlib import Path

from exlibris.extractors.base import BookMetadata, _first_str


def extract_mobi(path: Path) -> BookMetadata:
    suffix = path.suffix.lower().lstrip(".")
    meta = BookMetadata(format=suffix if suffix in {"mobi", "azw3"} else "mobi")
    try:
        from ebookatty import fetch_metadata
    except ImportError as exc:
        meta.errors.append(f"mobi extractor unavailable: {exc}")
        return meta

    try:
        raw = fetch_metadata(str(path))
    except Exception as exc:
        meta.errors.append(f"mobi read failed: {exc}")
        return meta

    if not isinstance(raw, dict):
        meta.errors.append("mobi metadata returned unexpected type")
        return meta

    meta.title = _first_str(raw.get("title"), raw.get("updatedtitle"), raw.get("name"), path.stem)
    meta.authors = _first_str(raw.get("author"), raw.get("creator"))
    meta.publisher = _first_str(raw.get("publisher"))
    meta.published_date = _first_str(raw.get("published"))
    meta.isbn = _first_str(raw.get("isbn"))
    meta.language = _first_str(raw.get("language"), raw.get("langcode"))
    meta.description = _first_str(raw.get("description"))
    return meta
