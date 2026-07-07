"""Validate EPUB structure and readability without extra dependencies."""

from __future__ import annotations

import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from exlibris.ebook_meta import EbookMetaError, find_ebook_meta

CONTAINER_PATH = "META-INF/container.xml"
MIMETYPE_PATH = "mimetype"
OPF_NS = {
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
}
CONTAINER_NS = {"cn": "urn:oasis:names:tc:opendocument:xmlns:container"}
READABLE_MEDIA_TYPES = frozenset(
    {
        "application/xhtml+xml",
        "text/html",
        "application/html+xml",
    }
)


@dataclass
class EpubValidationResult:
    path: Path
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _zip_entry_name(name: str) -> str:
    return name.replace("\\", "/")


def _resolve_manifest_href(opf_path: str, href: str) -> str:
    href = unquote(href.strip())
    if href.startswith("/"):
        return _zip_entry_name(href.lstrip("/"))
    base = PurePosixPath(opf_path).parent
    resolved = base / PurePosixPath(href)
    parts: list[str] = []
    for part in resolved.parts:
        if part == "..":
            if parts:
                parts.pop()
            continue
        if part == ".":
            continue
        parts.append(part)
    return _zip_entry_name("/".join(parts))


def _open_epub_zip(path: Path) -> zipfile.ZipFile:
    """Open an EPUB ZIP, tolerating legacy non-UTF-8 member names."""
    last_error: Exception | None = None
    for encoding in ("utf-8", "cp437", "latin-1"):
        try:
            return zipfile.ZipFile(path, metadata_encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
        except UnicodeError as exc:
            last_error = exc
    if last_error is not None:
        raise last_error
    return zipfile.ZipFile(path, metadata_encoding="cp437")


def _zip_name_lookup(archive: zipfile.ZipFile) -> dict[str, str]:
    """Map normalized POSIX paths to the archive's stored member names."""
    lookup: dict[str, str] = {}
    for name in archive.namelist():
        normalized = _zip_entry_name(name)
        lookup.setdefault(normalized, name)
        lookup.setdefault(normalized.lower(), name)
    return lookup


def _read_zip_member(
    archive: zipfile.ZipFile,
    lookup: dict[str, str],
    member_path: str,
) -> bytes:
    actual = lookup.get(member_path) or lookup.get(member_path.lower())
    if actual is None:
        raise KeyError(member_path)
    return archive.read(actual)


def validate_epub_structure(path: Path) -> EpubValidationResult:
    """Check ZIP integrity, container, OPF, manifest, and spine item readability."""
    path = path.expanduser().resolve()
    result = EpubValidationResult(path=path, ok=True)

    if not path.is_file():
        result.ok = False
        result.errors.append("file not found")
        return result

    if path.stat().st_size == 0:
        result.ok = False
        result.errors.append("file is empty")
        return result

    if not zipfile.is_zipfile(path):
        result.ok = False
        result.errors.append("not a ZIP archive")
        return result

    try:
        with _open_epub_zip(path) as archive:
            bad_member = archive.testzip()
            if bad_member is not None:
                result.ok = False
                result.errors.append(f"corrupt ZIP member: {bad_member}")
                return result

            lookup = _zip_name_lookup(archive)
            names = set(lookup.keys())

            if MIMETYPE_PATH in names:
                try:
                    mimetype = _read_zip_member(
                        archive, lookup, MIMETYPE_PATH
                    ).decode("utf-8", errors="replace").strip()
                    if mimetype != "application/epub+zip":
                        result.warnings.append(
                            f"unexpected mimetype: {mimetype!r}"
                        )
                except KeyError:
                    result.warnings.append("could not read mimetype")
            else:
                result.warnings.append("missing mimetype")

            if CONTAINER_PATH not in names:
                result.ok = False
                result.errors.append("missing META-INF/container.xml")
                return result

            try:
                container_xml = _read_zip_member(archive, lookup, CONTAINER_PATH)
            except KeyError:
                result.ok = False
                result.errors.append("could not read container.xml")
                return result

            try:
                container_root = ET.fromstring(container_xml)
            except ET.ParseError as exc:
                result.ok = False
                result.errors.append(f"invalid container.xml: {exc}")
                return result

            opf_path = None
            for rootfile in container_root.findall(".//cn:rootfile", CONTAINER_NS):
                full_path = rootfile.attrib.get("full-path", "").strip()
                if full_path:
                    opf_path = _zip_entry_name(full_path)
                    break
            if not opf_path:
                result.ok = False
                result.errors.append("container.xml has no rootfile")
                return result
            if opf_path not in names:
                result.ok = False
                result.errors.append(f"OPF not found in archive: {opf_path}")
                return result

            try:
                opf_xml = _read_zip_member(archive, lookup, opf_path)
            except KeyError:
                result.ok = False
                result.errors.append(f"could not read OPF: {opf_path}")
                return result

            try:
                opf_root = ET.fromstring(opf_xml)
            except ET.ParseError as exc:
                result.ok = False
                result.errors.append(f"invalid OPF XML: {exc}")
                return result

            manifest: dict[str, tuple[str, str]] = {}
            for item in opf_root.findall(".//opf:manifest/opf:item", OPF_NS):
                item_id = item.attrib.get("id", "").strip()
                href = item.attrib.get("href", "").strip()
                media_type = item.attrib.get("media-type", "").strip()
                if item_id and href:
                    manifest[item_id] = (href, media_type)

            spine_refs = [
                itemref.attrib.get("idref", "").strip()
                for itemref in opf_root.findall(".//opf:spine/opf:itemref", OPF_NS)
                if itemref.attrib.get("idref", "").strip()
            ]
            if not spine_refs:
                result.ok = False
                result.errors.append("OPF spine is empty")
                return result

            readable_found = False
            for idref in spine_refs:
                entry = manifest.get(idref)
                if entry is None:
                    result.ok = False
                    result.errors.append(f"spine itemref missing from manifest: {idref}")
                    continue
                href, media_type = entry
                member = _resolve_manifest_href(opf_path, href)
                if member not in names:
                    result.ok = False
                    result.errors.append(f"spine content missing from archive: {member}")
                    continue
                try:
                    content = _read_zip_member(archive, lookup, member)
                except KeyError:
                    result.ok = False
                    result.errors.append(f"could not read spine item: {member}")
                    continue
                if not content.strip():
                    result.ok = False
                    result.errors.append(f"spine item is empty: {member}")
                    continue
                if media_type in READABLE_MEDIA_TYPES or member.lower().endswith(
                    (".xhtml", ".html", ".htm")
                ):
                    readable_found = True
                    try:
                        ET.fromstring(content)
                    except ET.ParseError as exc:
                        result.ok = False
                        result.errors.append(
                            f"invalid spine markup in {member}: {exc}"
                        )

            if not readable_found:
                result.ok = False
                result.errors.append("spine has no readable HTML/XHTML content")

    except zipfile.BadZipFile:
        result.ok = False
        result.errors.append("bad ZIP archive")
    except UnicodeDecodeError as exc:
        result.ok = False
        result.errors.append(f"unreadable ZIP member names: {exc}")
    except OSError as exc:
        result.ok = False
        result.errors.append(str(exc))

    return result


def validate_epub_with_calibre(
    path: Path,
    *,
    ebook_meta_cmd: str | None = None,
) -> EpubValidationResult:
    """Run Calibre ebook-meta as an additional integrity check."""
    path = path.expanduser().resolve()
    result = validate_epub_structure(path)
    if not result.ok:
        return result

    cmd = find_ebook_meta(ebook_meta_cmd)
    try:
        proc = subprocess.run(
            [cmd, str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        result.warnings.append(f"ebook-meta not runnable: {exc}")
        return result

    if proc.returncode != 0:
        result.ok = False
        detail = (proc.stderr or proc.stdout or "").strip()
        result.errors.append(detail or f"ebook-meta exited with {proc.returncode}")
    return result


def validate_epub(
    path: Path,
    *,
    deep: bool = False,
    ebook_meta_cmd: str | None = None,
) -> EpubValidationResult:
    if deep:
        return validate_epub_with_calibre(path, ebook_meta_cmd=ebook_meta_cmd)
    return validate_epub_structure(path)


def format_validation_issue(result: EpubValidationResult) -> str:
    detail = "; ".join(result.errors) if result.errors else "invalid"
    return f"{result.path}: {detail}"


def find_ebook_meta_optional(explicit: str | None = None) -> str | None:
    if explicit:
        path = Path(explicit).expanduser()
        return str(path.resolve()) if path.exists() else None
    return shutil.which("ebook-meta")
