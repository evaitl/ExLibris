"""Tests for EPUB validation."""

from __future__ import annotations

import tempfile
import zlib
import zipfile
from pathlib import Path

from exlibris.epub_validate import _check_zip_integrity, _open_epub_zip, validate_epub_structure


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


def test_check_zip_integrity_handles_testzip_zlib_error() -> None:
    class BrokenArchive:
        def testzip(self) -> None:
            raise zlib.error("Error -3 while decompressing data: invalid stored block lengths")

    assert _check_zip_integrity(BrokenArchive()) == (
        "corrupt ZIP data: Error -3 while decompressing data: invalid stored block lengths"
    )


def test_validate_epub_structure_reports_testzip_failure(
    monkeypatch, tmp_path: Path
) -> None:
    path = tmp_path / "broken.epub"
    _write_minimal_epub(path)

    class BrokenArchive:
        def __enter__(self) -> BrokenArchive:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def testzip(self) -> None:
            raise zlib.error("invalid stored block lengths")

        def namelist(self) -> list[str]:
            return []

    def fake_open_epub_zip(_path: Path) -> BrokenArchive:
        return BrokenArchive()

    monkeypatch.setattr("exlibris.epub_validate._open_epub_zip", fake_open_epub_zip)

    result = validate_epub_structure(path)
    assert result.ok is False
    assert any("corrupt ZIP data" in error for error in result.errors)


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
