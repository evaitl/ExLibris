from pathlib import Path

from ebooklib import epub

from exlibris.extractors.base import BookMetadata, _first_str, _join_authors


def _meta_value(book: epub.EpubBook, namespace: str, name: str) -> str | None:
    values = book.get_metadata(namespace, name)
    if not values:
        return None
    for value, _attrs in values:
        text = _first_str(value)
        if text:
            return text
    return None


def extract_epub(path: Path) -> BookMetadata:
    meta = BookMetadata(format="epub")
    try:
        book = epub.read_epub(str(path), options={"ignore_ncx": True})
    except Exception as exc:
        meta.errors.append(f"epub read failed: {exc}")
        return meta

    meta.title = _first_str(
        _meta_value(book, "DC", "title"),
        path.stem,
    )
    meta.authors = _join_authors(
        [v for v, _ in book.get_metadata("DC", "creator")]
    )
    meta.publisher = _meta_value(book, "DC", "publisher")
    meta.published_date = _meta_value(book, "DC", "date")
    meta.isbn = _first_str(
        _meta_value(book, "DC", "identifier"),
    )
    meta.language = _meta_value(book, "DC", "language")
    meta.description = _meta_value(book, "DC", "description")
    return meta
