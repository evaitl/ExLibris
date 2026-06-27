from __future__ import annotations

import hashlib
import os
import shutil
import struct
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from exlibris.ebook_meta import EbookMeta, EbookMetaError, extract_cover, parse_opf

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_COVERS_DIR = PROJECT_ROOT / "data" / "covers"

MIN_COVER_BYTES = 500
MIN_COVER_WIDTH = 100
MIN_COVER_HEIGHT = 100
GOOGLE_COVER_ZOOM = 3
# Stable hashes for provider "no cover" images (full-size, not 1×1).
KNOWN_PLACEHOLDER_COVER_SHA256: frozenset[str] = frozenset(
    {
        # Google Books "image not available" (PNG disguised as JPEG URL, 575×750).
        "3efa8c43e5b4348f303a528c81adf435f0111ea752fe9f0f6241478b60987fa6",
        # Open Library missing-cover placeholder (JPEG, 192×262).
        "45ec8d2f9e3633f642da1f50946307e52bea6f1c8c697828074a3601f0312560",
    }
)
FETCH_PLUGINS = ("Google", "Open Library")
CALIBRE_FETCH_TIMEOUT = 30
SUBPROCESS_TIMEOUT = 45


def _resolve_covers_dir(path: Path | None = None) -> Path:
    if path is not None:
        covers = Path(path).expanduser()
        if not covers.is_absolute():
            covers = PROJECT_ROOT / covers
    else:
        env = os.environ.get("EXLIBRIS_COVERS_DIR")
        if env:
            covers = Path(env).expanduser()
        else:
            covers = DEFAULT_COVERS_DIR
    covers = covers.resolve()
    covers.mkdir(parents=True, exist_ok=True)
    return covers


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


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    index = 2
    while index < len(data) - 8:
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker in (
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        ):
            height = int.from_bytes(data[index + 5 : index + 7], "big")
            width = int.from_bytes(data[index + 7 : index + 9], "big")
            return width, height
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        segment_length = int.from_bytes(data[index + 2 : index + 4], "big")
        index += 2 + segment_length
    return None


def _cover_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _paeth_predictor(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _png_luminance_stats(data: bytes) -> tuple[float, float, float] | None:
    """Return mean luminance and bright/dark pixel ratios for an 8-bit PNG."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    pos = 8
    width = height = bit_depth = color_type = None
    idat = bytearray()
    while pos < len(data):
        chunk_len = int.from_bytes(data[pos : pos + 4], "big")
        chunk_type = data[pos + 4 : pos + 8]
        chunk = data[pos + 8 : pos + 8 + chunk_len]
        pos += 12 + chunk_len
        if chunk_type == b"IHDR" and chunk_len >= 13:
            width, height, bit_depth, color_type, _, _, _ = struct.unpack(
                ">IIBBBBB", chunk[:13]
            )
        elif chunk_type == b"IDAT":
            idat.extend(chunk)
        elif chunk_type == b"IEND":
            break
    if width is None or height is None or not idat:
        return None
    if bit_depth != 8 or color_type not in (0, 2, 3, 6):
        return None

    bytes_per_pixel = {0: 1, 2: 3, 3: 1, 6: 4}[color_type]
    row_bytes = width * bytes_per_pixel
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error:
        return None

    pixels: list[int] = []
    index = 0
    previous_row = bytearray(row_bytes)
    for _row in range(height):
        if index >= len(raw):
            return None
        filter_type = raw[index]
        index += 1
        filtered = bytearray(raw[index : index + row_bytes])
        index += row_bytes
        if len(filtered) < row_bytes:
            return None
        recon = bytearray(row_bytes)
        for i in range(row_bytes):
            left = recon[i - bytes_per_pixel] if i >= bytes_per_pixel else 0
            up = previous_row[i]
            up_left = previous_row[i - bytes_per_pixel] if i >= bytes_per_pixel else 0
            value = filtered[i]
            if filter_type == 1:
                value = (value + left) & 0xFF
            elif filter_type == 2:
                value = (value + up) & 0xFF
            elif filter_type == 3:
                value = (value + ((left + up) // 2)) & 0xFF
            elif filter_type == 4:
                value = (value + _paeth_predictor(left, up, up_left)) & 0xFF
            recon[i] = value
        previous_row = recon
        if color_type == 0:
            pixels.extend(recon)
        elif color_type == 2:
            for i in range(0, row_bytes, 3):
                red, green, blue = recon[i : i + 3]
                pixels.append((red + green + blue) // 3)
        elif color_type == 6:
            for i in range(0, row_bytes, 4):
                red, green, blue, _alpha = recon[i : i + 4]
                pixels.append((red + green + blue) // 3)
        else:
            return None

    if not pixels:
        return None
    bright = sum(1 for value in pixels if value >= 235)
    dark = sum(1 for value in pixels if value < 80)
    mean = sum(pixels) / len(pixels)
    return mean, bright / len(pixels), dark / len(pixels)


def _is_placeholder_cover(data: bytes) -> bool:
    """Detect provider placeholder art (e.g. Google 'image not available')."""
    if _cover_sha256(data) in KNOWN_PLACEHOLDER_COVER_SHA256:
        return True
    stats = _png_luminance_stats(data)
    if stats is None:
        return False
    mean, very_bright_ratio, dark_ratio = stats
    # Near-white PNG with almost no dark pixels (Google Books missing-cover art).
    return very_bright_ratio >= 0.88 and mean >= 248.0 and dark_ratio <= 0.01


def _image_dimensions(data: bytes) -> tuple[int, int] | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        return width, height
    if (data.startswith(b"GIF87a") or data.startswith(b"GIF89a")) and len(data) >= 10:
        width = int.from_bytes(data[6:8], "little")
        height = int.from_bytes(data[8:10], "little")
        return width, height
    if data.startswith(b"\xff\xd8"):
        return _jpeg_dimensions(data)
    return None


def _is_usable_cover(data: bytes) -> bool:
    """Reject tiny images and provider placeholder covers."""
    if len(data) < MIN_COVER_BYTES:
        return False
    if _is_placeholder_cover(data):
        return False
    dimensions = _image_dimensions(data)
    if dimensions is None:
        return False
    width, height = dimensions
    return width >= MIN_COVER_WIDTH and height >= MIN_COVER_HEIGHT


def _download_cover_url(url: str, *, reject_default: bool = False) -> bytes | None:
    if reject_default and "default=false" not in url:
        url = f"{url}{'&' if '?' in url else '?'}default=false"
    request = Request(url, headers={"User-Agent": "ExLibris/0.1"})
    try:
        with urlopen(request, timeout=30) as response:
            data = response.read()
    except HTTPError as exc:
        if exc.code == 404:
            return None
        return None
    except URLError:
        return None
    if not _is_usable_cover(data):
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
        cover = _download_cover_url(url, reject_default=True)
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
    covers_root = _resolve_covers_dir(covers_dir)
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


def restore_embedded_cover(
    *,
    ebook_path: Path,
    book_id: int,
    covers_dir: Path | None = None,
    ebook_meta_cmd: str | None = None,
) -> str:
    """Re-extract the cover embedded in the ebook file."""
    covers_root = _resolve_covers_dir(covers_dir)
    cover_file = extract_cover(
        ebook_path,
        covers_root / str(book_id),
        ebook_meta_cmd=ebook_meta_cmd,
    )
    if cover_file is None:
        raise FetchMetadataError("No embedded cover found in this ebook.")
    return str(cover_file.relative_to(PROJECT_ROOT))


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
