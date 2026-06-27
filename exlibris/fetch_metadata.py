from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from exlibris.config import PROJECT_ROOT, resolve_covers_dir
from exlibris.ebook_meta import EbookMeta, EbookMetaError, parse_opf

MIN_COVER_BYTES = 500
GOOGLE_COVER_ZOOM = 3
FETCH_PLUGINS = ("Google", "Open Library")
CALIBRE_FETCH_TIMEOUT = 30
SUBPROCESS_TIMEOUT = 45


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


def _calibre_subprocess_env(tmp_home: str) -> dict[str, str]:
    env = {
        "HOME": tmp_home,
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "QT_QPA_PLATFORM": "offscreen",
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }
    return env


def fetch_online_metadata(
    *,
    title: str | None,
    authors: str | None,
    isbn: str | None,
    fetch_cmd: str | None = None,
    timeout: int = SUBPROCESS_TIMEOUT,
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
    for plugin in FETCH_PLUGINS:
        args.extend(["-p", plugin])
    args.extend(["-d", str(CALIBRE_FETCH_TIMEOUT), "-o"])

    with tempfile.TemporaryDirectory(prefix="exlibris-calibre-") as tmp:
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
                env=_calibre_subprocess_env(tmp),
            )
        except subprocess.TimeoutExpired as exc:
            raise FetchMetadataError(
                "Timed out fetching metadata online "
                f"(>{timeout}s). Try again or check network access."
            ) from exc

        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise FetchMetadataError(
                detail or f"fetch-ebook-metadata exited with {result.returncode}"
            )

        opf_xml = result.stdout.strip()
        if not opf_xml:
            raise FetchMetadataError("fetch-ebook-metadata returned no metadata")

    try:
        meta = parse_opf(opf_xml)
    except EbookMetaError as exc:
        raise FetchMetadataError(str(exc)) from exc

    cover_bytes = _fetch_cover_fallback(opf_xml, meta.isbn or query_isbn)

    return meta, opf_xml, cover_bytes


COVER_EXTENSIONS = (".jpg", ".jpeg", ".png")


def _remove_existing_covers(covers_root: Path, book_id: int) -> None:
    """Remove prior cover files so a new file can be created with only dir write access."""
    for ext in COVER_EXTENSIONS:
        path = covers_root / f"{book_id}{ext}"
        if path.exists():
            path.unlink()


def _save_cover(
    cover_bytes: bytes,
    *,
    book_id: int,
    covers_dir: Path | None,
) -> str:
    covers_root = resolve_covers_dir(covers_dir)
    try:
        _remove_existing_covers(covers_root, book_id)
        dest = covers_root / f"{book_id}.jpg"
        dest.write_bytes(cover_bytes)
    except OSError as exc:
        raise FetchMetadataError(
            f"Cannot save cover image: {exc}. "
            f"The web server needs write permission on {covers_root}."
        ) from exc
    return str(dest.relative_to(PROJECT_ROOT))


def enrich_book_from_online(
    *,
    title: str | None,
    authors: str | None,
    isbn: str | None,
    book_id: int,
    covers_dir: Path | None = None,
    fetch_cmd: str | None = None,
) -> FetchResult:
    """Fetch metadata online and return database field updates."""
    meta, _opf_xml, cover_bytes = fetch_online_metadata(
        title=title,
        authors=authors,
        isbn=isbn,
        fetch_cmd=fetch_cmd,
    )

    fields = metadata_db_fields(meta)
    cover_updated = False

    if cover_bytes:
        cover_path = _save_cover(cover_bytes, book_id=book_id, covers_dir=covers_dir)
        fields["cover_path"] = cover_path
        cover_updated = True

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
