from pathlib import Path

from pypdf import PdfReader

from exlibris.extractors.base import BookMetadata, _first_str


def extract_pdf(path: Path) -> BookMetadata:
    meta = BookMetadata(format="pdf")
    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        meta.errors.append(f"pdf read failed: {exc}")
        return meta

    info = reader.metadata or {}
    meta.title = _first_str(info.get("/Title"), path.stem)
    meta.authors = _first_str(info.get("/Author"))
    meta.publisher = _first_str(info.get("/Producer"), info.get("/Creator"))
    meta.published_date = _first_str(info.get("/CreationDate"), info.get("/ModDate"))
    meta.description = _first_str(info.get("/Subject"))
    return meta
