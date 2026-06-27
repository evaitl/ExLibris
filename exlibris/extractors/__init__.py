from pathlib import Path

from exlibris.extractors.base import BookMetadata
from exlibris.extractors.epub import extract_epub
from exlibris.extractors.mobi import extract_mobi
from exlibris.extractors.pdf import extract_pdf

SUPPORTED_EXTENSIONS = {".epub", ".mobi", ".azw3", ".pdf"}


def extract_metadata(path: Path) -> BookMetadata:
    suffix = path.suffix.lower()
    if suffix == ".epub":
        return extract_epub(path)
    if suffix == ".pdf":
        return extract_pdf(path)
    if suffix in {".mobi", ".azw3"}:
        return extract_mobi(path)
    return BookMetadata(format=suffix.lstrip("."))
