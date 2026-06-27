from __future__ import annotations

import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from exlibris.ebook_meta import EbookMeta, EbookMetaError, apply_opf, parse_opf, set_cover

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_COVERS_DIR = PROJECT_ROOT / "covers"
MIN_COVER_BYTES = 500
GOOGLE_COVER_ZOOM = 3


class FetchMetadataError(Exception):
    pass


@dataclass
class FetchResult:
    fields: dict[str, object]
    cover_updated: bool = False


def find_fetch_ebook_metadata(explicit: str | None = None) -> str:
    if explicit:
        path = Path(explicit).expanduser()
        if not path.exists():
            raise FetchMetadataError(f"fetch-ebook-metadata not found at {path}")
        return str(path.resolve())
    found = shutil.which("fetch-ebook-metadata")
    if not found:
        raise FetchMetadataError(
            "fetch-ebook-metadata not found on PATH (install Calibre)"
        )
    return found


def _authors_for_fetch(authors: str | None) -> str | None:
    if not authors or not authors.strip():
        return None
    parts = [part.strip() for part in authors.split(";") if part.strip()]
    if not parts:
        return None
    return " & ".join(parts)


def resolve_covers_dir(covers_dir: Path | None = None) -> Path:
    covers = Path(covers_dir) if covers_dir else DEFAULT_COVERS_DIR
    if not covers.is_absolute():
        covers = PROJECT_ROOT / covers
    covers.mkdir(parents=True, exist_ok=True)
    return covers.resolve()


def _google_books_id_from_opf(opf_xml: str) -> str | None:
    root = ET.fromstring(opf_xml)
    ns = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}
    for el in root.findall(".//dc:identifier", ns):
        text = (el.text or "").strip()
        scheme = el.attrib.get("{http://www.idpf.org/2007/opf}scheme", "").upper()
        if scheme == "GOOGLE" and text:
            return text
    return None


def _download_cover_url(url: str) -> bytes | None:
    request = Request(url, headers={"User-Agent": "ExLibris/0.1"})
    try:
        with urlopen(request, timeout=30) as response:
            data = response.read()
    except URLError:
        return None
    if len(data) < MIN_COVER_BYTES:
        return None
    return data


def _fetch_cover_fallback(opf_xml: str, isbn: str | None) -> bytes | None:
    google_id = _google_books_id_from_opf(opf_xml)
    if google_id:
        url = (
            "https://books.google.com/books/content"
            f"?id={google_id}&printsec=frontcover&img=1&zoom={GOOGLE_COVER_ZOOM}"
        )
        cover = _download_cover_url(url)
        if cover:
            return cover

    if isbn:
        clean_isbn = isbn.replace("-", "").strip()
        url = f"https://covers.openlibrary.org/b/isbn/{clean_isbn}-L.jpg"
        cover = _download_cover_url(url)
        if cover:
            return cover

    return None


def fetch_online_metadata(
    *,
    title: str | None,
    authors: str | None,
    isbn: str | None,
    fetch_cmd: str | None = None,
    timeout: int = 60,
) -> tuple[EbookMeta, str, bytes | None]:
    """Return parsed metadata, raw OPF XML, and optional cover image bytes."""
    query_title = (title or "").strip() or None
    query_authors = _authors_for_fetch(authors)
    query_isbn = (isbn or "").strip() or None

    if not any((query_title, query_authors, query_isbn)):
        raise FetchMetadataError(
            "Need at least a title, author, or ISBN to fetch metadata online"
        )

    cmd = find_fetch_ebook_metadata(fetch_cmd)
    args = [cmd]
    if query_title:
        args.extend(["-t", query_title])
    if query_authors:
        args.extend(["-a", query_authors])
    if query_isbn:
        args.extend(["-i", query_isbn])
    args.append("-o")

    cover_bytes: bytes | None = None
    with tempfile.TemporaryDirectory() as tmp:
        cover_candidate = Path(tmp) / "cover.jpg"
        args.extend(["-c", str(cover_candidate)])

        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise FetchMetadataError(
                detail or f"fetch-ebook-metadata exited with {result.returncode}"
            )

        opf_xml = result.stdout.strip()
        if not opf_xml:
            raise FetchMetadataError("fetch-ebook-metadata returned no metadata")

        if cover_candidate.exists() and cover_candidate.stat().st_size >= MIN_COVER_BYTES:
            cover_bytes = cover_candidate.read_bytes()

    try:
        meta = parse_opf(opf_xml)
    except EbookMetaError as exc:
        raise FetchMetadataError(str(exc)) from exc

    if cover_bytes is None:
        cover_bytes = _fetch_cover_fallback(opf_xml, meta.isbn or query_isbn)

    return meta, opf_xml, cover_bytes


def _save_cover(
    cover_bytes: bytes,
    *,
    book_id: int,
    covers_dir: Path | None,
) -> str:
    covers_root = resolve_covers_dir(covers_dir)
    dest = covers_root / f"{book_id}.jpg"
    dest.write_bytes(cover_bytes)
    return str(dest.relative_to(PROJECT_ROOT))


def enrich_book_from_online(
    book_file: Path,
    *,
    title: str | None,
    authors: str | None,
    isbn: str | None,
    book_id: int,
    covers_dir: Path | None = None,
    ebook_meta_cmd: str | None = None,
    fetch_cmd: str | None = None,
) -> FetchResult:
    """Fetch metadata online, update the ebook file, and return DB field updates."""
    meta, opf_xml, cover_bytes = fetch_online_metadata(
        title=title,
        authors=authors,
        isbn=isbn,
        fetch_cmd=fetch_cmd,
    )

    try:
        apply_opf(book_file, opf_xml, ebook_meta_cmd=ebook_meta_cmd)
    except EbookMetaError as exc:
        raise FetchMetadataError(f"Failed to update ebook file: {exc}") from exc

    fields = metadata_db_fields(meta)
    cover_updated = False

    if cover_bytes:
        cover_path = _save_cover(cover_bytes, book_id=book_id, covers_dir=covers_dir)
        fields["cover_path"] = cover_path
        cover_updated = True
        try:
            set_cover(
                book_file,
                PROJECT_ROOT / cover_path,
                ebook_meta_cmd=ebook_meta_cmd,
            )
        except EbookMetaError as exc:
            raise FetchMetadataError(f"Failed to embed cover in ebook: {exc}") from exc

    return FetchResult(fields=fields, cover_updated=cover_updated)


def metadata_db_fields(meta: EbookMeta) -> dict[str, object]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "title": meta.title,
        "sort_title": meta.sort_title,
        "authors": meta.authors,
        "publisher": meta.publisher,
        "published_date": meta.published_date,
        "isbn": meta.isbn,
        "language": meta.language,
        "description": meta.description,
        "series": meta.series,
        "series_index": meta.series_index,
        "tags": meta.tags,
        "last_scanned_at": now,
    }
