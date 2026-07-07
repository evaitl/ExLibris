"""Tests for EPUB validation."""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

from exlibris.epub_validate import _open_epub_zip, validate_epub_structure


def _write_minimal_epub(path: Path, *, include_spine: bool = True) -> None:
    container = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""
    opf = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Sample</dc:title>
  </metadata>
  <manifest>
    <item id="chapter1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="chapter1"/>
  </spine>
</package>"""
    if not include_spine:
        opf = opf.replace(
            "<spine>\n    <itemref idref=\"chapter1\"/>\n  </spine>",
            "<spine></spine>",
        )
    chapter = """<?xml version="1.0"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>Chapter</title></head>
  <body><p>Hello</p></body>
</html>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/chapter1.xhtml", chapter)


def test_open_epub_zip_falls_back_when_utf8_decode_fails(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / "legacy.epub"
    _write_minimal_epub(path)
    calls: list[str] = []
    real_zipfile = zipfile.ZipFile

    def fake_zipfile(
        file: Path,
        metadata_encoding: str = "utf-8",
    ) -> zipfile.ZipFile:
        calls.append(metadata_encoding)
        if metadata_encoding == "utf-8":
            raise UnicodeDecodeError("utf-8", b"\xbf", 2, 3, "invalid start byte")
        return real_zipfile(file, metadata_encoding=metadata_encoding)

    monkeypatch.setattr("exlibris.epub_validate.zipfile.ZipFile", fake_zipfile)

    with _open_epub_zip(path) as archive:
        assert "META-INF/container.xml" in archive.namelist()

    assert calls[:2] == ["utf-8", "cp437"]


def test_validate_epub_structure_accepts_minimal_epub() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "good.epub"
        _write_minimal_epub(path)
        result = validate_epub_structure(path)
        assert result.ok is True
        assert result.errors == []


def test_validate_epub_structure_rejects_non_zip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "bad.epub"
        path.write_text("not a zip", encoding="utf-8")
        result = validate_epub_structure(path)
        assert result.ok is False
        assert any("ZIP" in error for error in result.errors)


def test_validate_epub_structure_rejects_empty_spine() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "empty-spine.epub"
        _write_minimal_epub(path, include_spine=False)
        result = validate_epub_structure(path)
        assert result.ok is False
        assert any("spine" in error.lower() for error in result.errors)
