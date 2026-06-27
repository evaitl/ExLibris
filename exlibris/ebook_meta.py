from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


class EbookMetaError(Exception):
    pass


@dataclass
class EbookMeta:
    title: str | None = None
    sort_title: str | None = None
    authors: str | None = None
    publisher: str | None = None
    published_date: str | None = None
    isbn: str | None = None
    language: str | None = None
    description: str | None = None
    series: str | None = None
    series_index: float | None = None
    tags: str | None = None
    format: str = ""
    errors: list[str] = field(default_factory=list)


def find_ebook_meta(explicit: str | None = None) -> str:
    if explicit:
        path = Path(explicit).expanduser()
        if not path.exists():
            raise EbookMetaError(f"ebook-meta not found at {path}")
        return str(path.resolve())
    found = shutil.which("ebook-meta")
    if not found:
        raise EbookMetaError(
            "ebook-meta not found on PATH (install Calibre or pass --ebook-meta)"
        )
    return found


def read_metadata(path: Path, *, ebook_meta_cmd: str | None = None) -> EbookMeta:
    path = path.expanduser().resolve()
    cmd = find_ebook_meta(ebook_meta_cmd)
    meta = EbookMeta(format=path.suffix.lower().lstrip("."))

    try:
        opf_meta = _read_via_opf(path, cmd)
        if opf_meta is not None:
            opf_meta.format = meta.format
            return opf_meta
    except EbookMetaError as exc:
        meta.errors.append(str(exc))

    try:
        text_meta = _read_via_text(path, cmd)
        text_meta.format = meta.format
        if meta.errors:
            text_meta.errors = meta.errors + text_meta.errors
        return text_meta
    except EbookMetaError as exc:
        meta.errors.append(str(exc))
        return meta


def _run_ebook_meta(cmd: str, args: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [cmd, *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise EbookMetaError(detail or f"ebook-meta exited with {result.returncode}")
    return result


def _read_via_opf(path: Path, cmd: str) -> EbookMeta | None:
    with tempfile.TemporaryDirectory() as tmp:
        opf_path = Path(tmp) / "metadata.opf"
        _run_ebook_meta(cmd, [str(path), "--to-opf", str(opf_path)])
        if not opf_path.exists() or opf_path.stat().st_size == 0:
            return None
        return _parse_opf(opf_path.read_text(encoding="utf-8", errors="replace"))


def _read_via_text(path: Path, cmd: str) -> EbookMeta:
    result = _run_ebook_meta(cmd, [str(path)])
    return _parse_text_output(result.stdout)


def _parse_text_output(text: str) -> EbookMeta:
    meta = EbookMeta()
    fields: dict[str, str] = {}

    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip().lower()] = value.strip()

    meta.title = fields.get("title") or None
    meta.sort_title = fields.get("title sort") or None
    if authors := fields.get("author(s)"):
        meta.authors = _normalize_authors(authors)
    meta.publisher = fields.get("publisher") or None
    meta.published_date = fields.get("published") or None
    meta.language = _first_token(fields.get("languages"))
    meta.description = fields.get("comments") or None
    meta.series = fields.get("series") or None
    meta.tags = fields.get("tags") or None
    meta.isbn = _parse_isbn(fields.get("identifiers"))
    if series_index := fields.get("number in series"):
        meta.series_index = _parse_float(series_index)
    return meta


def _parse_opf(xml_text: str) -> EbookMeta:
    root = ET.fromstring(xml_text)
    ns = {
        "opf": "http://www.idpf.org/2007/opf",
        "dc": "http://purl.org/dc/elements/1.1/",
    }

    metadata = root.find(".//opf:metadata", ns)
    if metadata is None:
        raise EbookMetaError("OPF metadata section missing")

    meta = EbookMeta()
    meta.title = _first_text(metadata, "dc:title", ns)
    meta.sort_title = _attr(metadata, "meta", ns, name="calibre:title_sort")
    creators = [
        el.text.strip()
        for el in metadata.findall("dc:creator", ns)
        if el.text and el.text.strip()
    ]
    meta.authors = _join_authors(creators)
    meta.publisher = _first_text(metadata, "dc:publisher", ns)
    meta.published_date = _first_text(metadata, "dc:date", ns)
    meta.language = _first_text(metadata, "dc:language", ns)
    meta.description = _first_text(metadata, "dc:description", ns)
    meta.series = _attr(metadata, "meta", ns, name="calibre:series")
    meta.series_index = _parse_float(
        _attr(metadata, "meta", ns, name="calibre:series_index")
    )
    subjects = [
        el.text.strip()
        for el in metadata.findall("dc:subject", ns)
        if el.text and el.text.strip()
    ]
    meta.tags = ", ".join(subjects) if subjects else None
    meta.isbn = _isbn_from_identifiers(metadata, ns)
    return meta


def _first_text(parent: ET.Element, tag: str, ns: dict[str, str]) -> str | None:
    for el in parent.findall(tag, ns):
        if el.text and el.text.strip():
            return el.text.strip()
    return None


def _attr(
    parent: ET.Element,
    tag: str,
    ns: dict[str, str],
    *,
    name: str,
) -> str | None:
    for el in parent.findall(tag, ns):
        if el.attrib.get("name") == name and el.attrib.get("content"):
            return el.attrib["content"].strip()
    return None


def _isbn_from_identifiers(parent: ET.Element, ns: dict[str, str]) -> str | None:
    for el in parent.findall("dc:identifier", ns):
        text = (el.text or "").strip()
        scheme = el.attrib.get("{http://www.idpf.org/2007/opf}scheme", "").lower()
        if scheme == "isbn" and text:
            return text
        if text.lower().startswith("isbn:"):
            return text.split(":", 1)[1].strip()
        if re.fullmatch(r"97[89]\d{10}|\d{9}[\dXx]", text):
            return text
    return None


def _normalize_authors(value: str) -> str | None:
    parts = [part.strip() for part in value.split("&")]
    return _join_authors(parts)


def _join_authors(parts: list[str]) -> str | None:
    cleaned = [part for part in parts if part]
    return "; ".join(cleaned) if cleaned else None


def _first_token(value: str | None) -> str | None:
    if not value:
        return None
    token = value.split(",")[0].strip()
    return token or None


def _parse_isbn(identifiers: str | None) -> str | None:
    if not identifiers:
        return None
    for part in identifiers.split(","):
        part = part.strip()
        if part.lower().startswith("isbn:"):
            return part.split(":", 1)[1].strip()
    return None


def _parse_float(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def extract_cover(
    source: Path,
    dest_base: Path,
    *,
    ebook_meta_cmd: str | None = None,
) -> Path | None:
    """Extract embedded cover via ebook-meta --get-cover. Returns saved file path."""
    source = source.expanduser().resolve()
    cmd = find_ebook_meta(ebook_meta_cmd)
    dest_base.parent.mkdir(parents=True, exist_ok=True)

    for ext in (".jpg", ".jpeg", ".png"):
        existing = dest_base.with_suffix(ext)
        if existing.exists():
            existing.unlink()

    dest = dest_base.with_suffix(".jpg")
    try:
        _run_ebook_meta(cmd, [str(source), "--get-cover", str(dest)])
    except EbookMetaError:
        return None

    if dest.exists() and dest.stat().st_size > 0:
        return dest

    if dest.exists():
        dest.unlink()
    return None
