#!/usr/bin/env python3
"""Convert EPUB(s) to EPUB 2 in place using Calibre ebook-convert."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

EPUB2_OPTIONS = (
    "--epub-version=2",
    "--no-svg-cover",
)
EPUB_MODE = 0o644
CONTAINER_PATH = "META-INF/container.xml"
XLINK_NS = "http://www.w3.org/1999/xlink"
IMAGE_MEDIA_TYPES = frozenset(
    {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/gif",
        "image/webp",
    }
)
COVER_ITEM_IDS = frozenset({"cover", "cover-image", "coverimage", "cover_image"})
ENCRYPTION_MEMBER = "META-INF/encryption.xml"
ADOBE_FONT_OBFUSCATION = "http://ns.adobe.com/pdf/enc#RC"
IDPF_FONT_OBFUSCATION = "http://www.idpf.org/2008/embedding"
FONT_OBFUSCATION_ALGORITHMS = frozenset(
    {ADOBE_FONT_OBFUSCATION, IDPF_FONT_OBFUSCATION}
)
FONT_MAGIC = (
    b"\x00\x01\x00\x00",
    b"OTTO",
    b"true",
    b"typ1",
    b"wOFF",
    b"wOF2",
)


def find_tool(name: str, explicit: str | None) -> str:
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"{name} not found: {path}")
        return str(path.resolve())
    found = shutil.which(name)
    if not found:
        raise FileNotFoundError(
            f"{name} not found on PATH (install Calibre or pass --{name.replace('-', '-')})"
        )
    return found


def iter_epubs(target: Path) -> list[Path]:
    target = target.expanduser().resolve()
    if target.is_file():
        if target.suffix.lower() != ".epub":
            raise ValueError(f"not an EPUB file: {target}")
        return [target]
    if not target.is_dir():
        raise FileNotFoundError(f"not found: {target}")
    return sorted(
        path.resolve()
        for path in target.rglob("*")
        if path.is_file() and path.suffix.lower() == ".epub"
    )


def extract_cover(epub: Path, dest: Path, *, ebook_meta_cmd: str) -> bool:
    result = subprocess.run(
        [ebook_meta_cmd, str(epub), "--get-cover", str(dest)],
        capture_output=True,
        text=True,
        check=False,
    )
    return (
        result.returncode == 0
        and dest.is_file()
        and dest.stat().st_size > 0
    )


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _zip_name(name: str) -> str:
    return name.replace("\\", "/")


def _resolve_href(base: str, href: str) -> str:
    href = unquote(href.strip().split("#", 1)[0])
    if not href:
        return ""
    if href.startswith("/"):
        resolved = PurePosixPath(href.lstrip("/"))
    else:
        resolved = PurePosixPath(base).parent / href
    parts: list[str] = []
    for part in resolved.parts:
        if part == "..":
            if parts:
                parts.pop()
            continue
        if part in (".", ""):
            continue
        parts.append(part)
    return "/".join(parts)


def _opf_path(archive: zipfile.ZipFile) -> str | None:
    try:
        container = archive.read(CONTAINER_PATH)
    except KeyError:
        return None
    try:
        root = ET.fromstring(container)
    except ET.ParseError:
        return None
    for element in root.iter():
        if _local_tag(element.tag) != "rootfile":
            continue
        full_path = element.get("full-path")
        if full_path:
            return _zip_name(full_path)
    return None


def _manifest_items(root: ET.Element) -> list[ET.Element]:
    for child in root:
        if _local_tag(child.tag) == "manifest":
            return [el for el in child if _local_tag(el.tag) == "item"]
    return []


def _metadata(root: ET.Element) -> ET.Element | None:
    for child in root:
        if _local_tag(child.tag) == "metadata":
            return child
    return None


def _is_image_item(item: ET.Element) -> bool:
    return (item.get("media-type") or "").lower() in IMAGE_MEDIA_TYPES


def _item_by_id(items: list[ET.Element], item_id: str | None) -> ET.Element | None:
    if not item_id:
        return None
    for item in items:
        if item.get("id") == item_id:
            return item
    return None


def _cover_id_from_metadata(metadata: ET.Element | None) -> str | None:
    if metadata is None:
        return None
    for meta in metadata:
        if _local_tag(meta.tag) != "meta":
            continue
        if (meta.get("name") or "").lower() == "cover":
            content = (meta.get("content") or "").strip()
            if content:
                return content
    return None


def _image_href_from_document(xml_bytes: bytes) -> str | None:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None
    for element in root.iter():
        local = _local_tag(element.tag)
        if local == "img":
            src = element.get("src")
            if src:
                return src
        if local == "image":
            href = element.get("href") or element.get(f"{{{XLINK_NS}}}href")
            if href:
                return href
    return None


def _find_cover_item(
    archive: zipfile.ZipFile,
    root: ET.Element,
    opf_path: str,
) -> ET.Element | None:
    items = _manifest_items(root)
    if not items:
        return None

    cover_id = _cover_id_from_metadata(_metadata(root))
    referenced = _item_by_id(items, cover_id)
    if referenced is not None and _is_image_item(referenced):
        return referenced

    for item in items:
        properties = (item.get("properties") or "").split()
        if "cover-image" in properties and _is_image_item(item):
            return item

    if referenced is not None:
        href = referenced.get("href")
        if href:
            member = _resolve_href(opf_path, href)
            try:
                doc = archive.read(member)
            except KeyError:
                doc = b""
            image_href = _image_href_from_document(doc) if doc else None
            if image_href:
                image_member = _resolve_href(member, image_href)
                for item in items:
                    item_href = item.get("href")
                    if (
                        item_href
                        and _is_image_item(item)
                        and _resolve_href(opf_path, item_href) == image_member
                    ):
                        return item

    for item in items:
        if not _is_image_item(item):
            continue
        item_id = (item.get("id") or "").lower()
        href = (item.get("href") or "").replace("\\", "/").lower()
        name = href.rsplit("/", 1)[-1]
        if item_id in COVER_ITEM_IDS or name.startswith("cover."):
            return item

    return None


def _repair_broken_cover_item(opf_text: str) -> str:
    """Fix ``<item ..."/ properties="cover-image">`` from the old rewriter."""
    return re.sub(
        r'(<item\b[^>]*?)/\s+properties="cover-image">',
        r'\1 properties="cover-image"/>',
        opf_text,
        flags=re.IGNORECASE,
    )


def _open_tag_re(tag: str, id_attr: str, id_value: str) -> re.Pattern[str]:
    """Match a start or self-closing tag without swallowing the final '/'."""
    return re.compile(
        rf'(<{tag}\b(?=[^>]*\b{id_attr}=["\']{re.escape(id_value)}["\'])[^>]*?)\s*(/?>)',
        re.IGNORECASE,
    )


def _ensure_cover_meta(opf_text: str, item_id: str) -> str:
    meta_re = _open_tag_re("meta", "name", "cover")
    match = meta_re.search(opf_text)
    if match:
        tag, ending = match.group(1), match.group(2)
        if re.search(r'\bcontent=["\']', tag, re.IGNORECASE):
            tag = re.sub(
                r'\bcontent=["\'][^"\']*["\']',
                f'content="{item_id}"',
                tag,
                count=1,
                flags=re.IGNORECASE,
            )
        else:
            tag = tag.rstrip() + f' content="{item_id}"'
        return opf_text[: match.start()] + tag + ending + opf_text[match.end() :]

    metadata_re = re.compile(r"(<metadata\b[^>]*>)", re.IGNORECASE)
    return metadata_re.sub(
        rf'\1<meta name="cover" content="{item_id}"/>',
        opf_text,
        count=1,
    )


def _ensure_cover_image_property(opf_text: str, item_id: str) -> str:
    item_re = _open_tag_re("item", "id", item_id)
    match = item_re.search(opf_text)
    if match is None:
        return opf_text

    tag, ending = match.group(1), match.group(2)
    props = re.search(r'\bproperties=["\']([^"\']*)["\']', tag, re.IGNORECASE)
    if props:
        values = props.group(1).split()
        if "cover-image" not in values:
            values.append("cover-image")
            tag = (
                tag[: props.start()]
                + f'properties="{" ".join(values)}"'
                + tag[props.end() :]
            )
    else:
        tag = tag.rstrip() + ' properties="cover-image"'
    return opf_text[: match.start()] + tag + ending + opf_text[match.end() :]


def _write_zip_member(
    dst: zipfile.ZipFile,
    name: str,
    payload: bytes,
    *,
    stored: bool = False,
    date_time: tuple[int, ...] | None = None,
) -> None:
    info = zipfile.ZipInfo(name, date_time=date_time or (2020, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
    info.extra = b""
    dst.writestr(info, payload)


def _replace_zip_member(epub: Path, member: str, data: bytes) -> None:
    handle, tmp_name = tempfile.mkstemp(suffix=".epub", dir=str(epub.parent))
    os.close(handle)
    tmp = Path(tmp_name)
    try:
        with zipfile.ZipFile(epub, "r") as src, zipfile.ZipFile(tmp, "w") as dst:
            names = src.namelist()
            mime = src.read("mimetype") if "mimetype" in names else b"application/epub+zip"
            _write_zip_member(dst, "mimetype", mime, stored=True)
            written = {"mimetype"}
            for info in src.infolist():
                name = info.filename
                if name in written or name.endswith("/"):
                    continue
                payload = data if name == member else src.read(name)
                _write_zip_member(
                    dst,
                    name,
                    payload,
                    stored=False,
                    date_time=info.date_time,
                )
                written.add(name)
            if member not in written:
                _write_zip_member(dst, member, data)
        os.replace(tmp, epub)
        tmp = None
    finally:
        if tmp is not None and tmp.is_file():
            tmp.unlink()


def mark_cover_for_thumbnailers(epub: Path) -> bool:
    """Tag the raster cover so file-browser thumbnailers can find it.

    EPUB 2 conversion drops ``properties="cover-image"`` and often leaves
    ``meta name="cover"`` pointing at an HTML title page. GNOME/KDE
    thumbnailers look for those markers on a JPEG/PNG item.
    """
    epub = epub.expanduser().resolve()
    if not zipfile.is_zipfile(epub):
        return False
    try:
        with zipfile.ZipFile(epub, "r") as archive:
            opf_path = _opf_path(archive)
            if not opf_path:
                return False
            try:
                opf_bytes = archive.read(opf_path)
            except KeyError:
                return False
            opf_text = _repair_broken_cover_item(opf_bytes.decode("utf-8"))
            try:
                root = ET.fromstring(opf_text)
            except ET.ParseError:
                return False
            cover_item = _find_cover_item(archive, root, opf_path)
            if cover_item is None:
                return False
            item_id = cover_item.get("id")
            if not item_id:
                return False
    except OSError:
        return False

    patched = _ensure_cover_meta(opf_text, item_id)
    patched = _ensure_cover_image_property(patched, item_id)
    if patched == opf_text:
        return True
    try:
        _replace_zip_member(epub, opf_path, patched.encode("utf-8"))
    except OSError:
        return False
    return True


def _zip_member_lookup(archive: zipfile.ZipFile) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for name in archive.namelist():
        normalized = _zip_name(name)
        lookup.setdefault(normalized, name)
        lookup.setdefault(normalized.lower(), name)
    return lookup


def _read_member(archive: zipfile.ZipFile, lookup: dict[str, str], path: str) -> bytes | None:
    actual = lookup.get(path) or lookup.get(path.lower())
    if actual is None:
        return None
    try:
        return archive.read(actual)
    except KeyError:
        return None


def _has_encryption_xml(epub: Path) -> bool:
    try:
        with zipfile.ZipFile(epub, "r") as archive:
            lookup = _zip_member_lookup(archive)
            return _read_member(archive, lookup, ENCRYPTION_MEMBER) is not None
    except (OSError, zipfile.BadZipFile):
        return False


def _looks_like_font(data: bytes) -> bool:
    return any(data.startswith(magic) for magic in FONT_MAGIC)


def _looks_like_markup(data: bytes) -> bool:
    if not data or not data.lstrip().startswith(b"<"):
        return False
    try:
        ET.fromstring(data)
        return True
    except ET.ParseError:
        sample = data[:4096].lower()
        return b"<html" in sample or b"<body" in sample or sample.lstrip().startswith(b"<?xml")


def _spine_content_is_readable(archive: zipfile.ZipFile) -> bool:
    lookup = _zip_member_lookup(archive)
    opf_path = _opf_path(archive)
    if not opf_path:
        return False
    opf_bytes = _read_member(archive, lookup, opf_path)
    if not opf_bytes:
        return False
    try:
        root = ET.fromstring(opf_bytes)
    except ET.ParseError:
        return False
    items = {
        item.get("id"): item
        for item in _manifest_items(root)
        if item.get("id")
    }
    for child in root:
        if _local_tag(child.tag) != "spine":
            continue
        for itemref in child:
            if _local_tag(itemref.tag) != "itemref":
                continue
            item = items.get(itemref.get("idref"))
            if item is None:
                continue
            href = item.get("href")
            media_type = (item.get("media-type") or "").lower()
            if not href:
                continue
            member = _resolve_href(opf_path, href)
            data = _read_member(archive, lookup, member)
            if not data:
                continue
            if media_type in {
                "application/xhtml+xml",
                "text/html",
                "application/html+xml",
            } or member.lower().endswith((".xhtml", ".html", ".htm")):
                if _looks_like_markup(data):
                    return True
    return False


def _identifier_texts(root: ET.Element) -> list[str]:
    metadata = _metadata(root)
    if metadata is None:
        return []
    texts: list[str] = []
    for element in metadata:
        if _local_tag(element.tag) != "identifier":
            continue
        text = (element.text or "").strip()
        if text:
            texts.append(text)
    return texts


def _package_unique_identifier(root: ET.Element) -> str | None:
    uid = root.get("unique-identifier")
    metadata = _metadata(root)
    if metadata is not None and uid:
        for element in metadata:
            if _local_tag(element.tag) != "identifier":
                continue
            if element.get("id") == uid:
                text = (element.text or "").strip()
                if text:
                    return text
    texts = _identifier_texts(root)
    return texts[0] if texts else None


def _idpf_font_key(identifier: str | None) -> bytes | None:
    if not identifier:
        return None
    compact = re.sub(r"[ \t\r\n]", "", identifier)
    if not compact:
        return None
    return hashlib.sha1(compact.encode("utf-8")).digest()


def _adobe_font_key(identifiers: list[str]) -> bytes | None:
    for text in identifiers:
        raw = text.strip()
        if raw.lower().startswith("urn:uuid:"):
            raw = raw.rsplit(":", 1)[-1]
        try:
            return uuid.UUID(raw).bytes
        except ValueError:
            continue
    return None


def _xor_prefix(data: bytes, key: bytes, length: int) -> bytes:
    buf = bytearray(data)
    limit = min(length, len(buf))
    key_len = len(key)
    for index in range(limit):
        buf[index] ^= key[index % key_len]
    return bytes(buf)


def _deobfuscate_font(data: bytes, algorithm: str, *, idpf_key: bytes | None, adobe_key: bytes | None) -> bytes:
    if _looks_like_font(data):
        return data
    if algorithm == IDPF_FONT_OBFUSCATION and idpf_key:
        candidate = _xor_prefix(data, idpf_key, 1040)
        if _looks_like_font(candidate):
            return candidate
    if algorithm == ADOBE_FONT_OBFUSCATION and adobe_key:
        candidate = _xor_prefix(data, adobe_key, 1024)
        if _looks_like_font(candidate):
            return candidate
    return data


def _encryption_entries(xml_bytes: bytes) -> list[tuple[str, str]]:
    root = ET.fromstring(xml_bytes)
    entries: list[tuple[str, str]] = []
    for element in root.iter():
        if _local_tag(element.tag) != "EncryptedData":
            continue
        algorithm = ""
        uri = ""
        for child in element.iter():
            local = _local_tag(child.tag)
            if local == "EncryptionMethod":
                algorithm = child.get("Algorithm") or ""
            elif local == "CipherReference":
                uri = unquote(child.get("URI") or "").strip()
        if uri:
            entries.append((algorithm, uri))
    return entries


def _write_calibre_readable_epub(source: Path, dest: Path) -> bool:
    """Copy an EPUB without stale encryption.xml so Calibre will convert it.

    Calibre treats any unrecognized ``META-INF/encryption.xml`` as DRM, including
    IDPF/Adobe font obfuscation it fails to unwrap and leftover encryption
    entries on plaintext files. Readers like xreader still open those books.
    Real content DRM (unreadable spine HTML) is left untouched.
    """
    try:
        with zipfile.ZipFile(source, "r") as src:
            lookup = _zip_member_lookup(src)
            enc_bytes = _read_member(src, lookup, ENCRYPTION_MEMBER)
            if enc_bytes is None:
                return False
            if not _spine_content_is_readable(src):
                return False

            replacements: dict[str, bytes] = {}
            opf_path = _opf_path(src)
            idpf_key = adobe_key = None
            if opf_path:
                opf_bytes = _read_member(src, lookup, opf_path)
                if opf_bytes:
                    try:
                        opf_root = ET.fromstring(opf_bytes)
                    except ET.ParseError:
                        opf_root = None
                    if opf_root is not None:
                        identifiers = _identifier_texts(opf_root)
                        unique = _package_unique_identifier(opf_root)
                        idpf_key = _idpf_font_key(unique)
                        adobe_key = _adobe_font_key(
                            [unique] + identifiers if unique else identifiers
                        )
            try:
                entries = _encryption_entries(enc_bytes)
            except ET.ParseError:
                entries = []
            for algorithm, uri in entries:
                if algorithm not in FONT_OBFUSCATION_ALGORITHMS:
                    continue
                member = lookup.get(uri) or lookup.get(uri.lower())
                if member is None:
                    continue
                data = src.read(member)
                replacements[member] = _deobfuscate_font(
                    data, algorithm, idpf_key=idpf_key, adobe_key=adobe_key
                )

            skip = {ENCRYPTION_MEMBER.lower(), "meta-inf/rights.xml"}
            with zipfile.ZipFile(dest, "w") as dst:
                names = src.namelist()
                mime = (
                    src.read("mimetype")
                    if "mimetype" in names
                    else b"application/epub+zip"
                )
                _write_zip_member(dst, "mimetype", mime, stored=True)
                written = {"mimetype"}
                for info in src.infolist():
                    name = info.filename
                    if name in written or name.endswith("/"):
                        continue
                    if _zip_name(name).lower() in skip:
                        continue
                    payload = replacements.get(name, src.read(name))
                    _write_zip_member(
                        dst,
                        name,
                        payload,
                        stored=False,
                        date_time=info.date_time,
                    )
                    written.add(name)
    except (OSError, zipfile.BadZipFile, KeyError):
        return False
    return dest.is_file() and dest.stat().st_size > 0


def convert_in_place(
    epub: Path,
    *,
    convert_cmd: str,
    meta_cmd: str,
    dry_run: bool,
    verbose: bool,
) -> bool:
    if dry_run:
        print(f"would convert: {epub}")
        return True

    temp_epub: Path | None = None
    temp_cover: Path | None = None
    cleaned_source: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".epub",
            dir=epub.parent,
            delete=False,
        ) as handle:
            temp_epub = Path(handle.name)

        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as cover_handle:
            temp_cover = Path(cover_handle.name)

        source_epub = epub
        if _has_encryption_xml(epub):
            with tempfile.NamedTemporaryFile(
                suffix=".epub",
                dir=epub.parent,
                delete=False,
            ) as cleaned_handle:
                cleaned_source = Path(cleaned_handle.name)
            if _write_calibre_readable_epub(epub, cleaned_source):
                source_epub = cleaned_source
                if verbose:
                    print(f"stripped non-DRM encryption.xml: {epub}", file=sys.stderr)
            else:
                cleaned_source.unlink(missing_ok=True)
                cleaned_source = None

        options = list(EPUB2_OPTIONS)
        if extract_cover(source_epub, temp_cover, ebook_meta_cmd=meta_cmd):
            options.extend(["--cover", str(temp_cover)])
        elif verbose:
            print(f"no cover extracted: {epub}", file=sys.stderr)

        result = subprocess.run(
            [convert_cmd, str(source_epub), str(temp_epub), *options],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(detail or f"exit code {result.returncode}")

        if not temp_epub.is_file() or temp_epub.stat().st_size == 0:
            raise RuntimeError("ebook-convert produced no output")

        mark_cover_for_thumbnailers(temp_epub)

        os.replace(temp_epub, epub)
        temp_epub = None
        os.chmod(epub, EPUB_MODE)

        if verbose:
            print(f"converted: {epub}")
        return True
    except (OSError, RuntimeError) as exc:
        print(f"failed: {epub}: {exc}", file=sys.stderr)
        return False
    finally:
        for path in (temp_epub, temp_cover, cleaned_source):
            if path is not None and path.is_file():
                try:
                    path.unlink()
                except OSError:
                    pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Convert one EPUB file or all EPUBs under a directory to EPUB 2 in place."
    )
    parser.add_argument("target", type=Path, help="EPUB file or directory")
    parser.add_argument("--ebook-convert", default=None, help="Path to ebook-convert")
    parser.add_argument("--ebook-meta", default=None, help="Path to ebook-meta")
    parser.add_argument("-n", "--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    try:
        convert_cmd = find_tool("ebook-convert", args.ebook_convert)
        meta_cmd = find_tool("ebook-meta", args.ebook_meta)
        epubs = iter_epubs(args.target)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not epubs:
        print(f"No EPUB files under {args.target.expanduser().resolve()}")
        return 0

    ok = failed = 0
    total = len(epubs)
    for index, epub in enumerate(epubs, start=1):
        if not args.verbose and not args.dry_run and total > 1:
            width = len(str(total))
            print(f"[{index:>{width}}/{total}] {epub.name}", flush=True)
        if convert_in_place(
            epub,
            convert_cmd=convert_cmd,
            meta_cmd=meta_cmd,
            dry_run=args.dry_run,
            verbose=args.verbose,
        ):
            ok += 1
        else:
            failed += 1

    verb = "would convert" if args.dry_run else "converted"
    print(f"{verb} {ok} EPUB(s); failed {failed} of {total}.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
