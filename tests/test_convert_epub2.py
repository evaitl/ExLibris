"""Tests for convert_epub2 cover tagging and in-place conversion."""

from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from exlibris.config import PROJECT_ROOT


def _load_convert_epub2():
    script = PROJECT_ROOT / "convert_epub2.py"
    spec = importlib.util.spec_from_file_location("convert_epub2_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


convert_epub2 = _load_convert_epub2()

CONTAINER = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

CHAPTER = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body><p>Hello</p></body>
</html>
"""

TITLEPAGE = """<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <body><img src="cover.jpg" alt="Cover"/></body>
</html>
"""


def _opf(*, cover_meta: str | None, cover_property: bool) -> str:
    props = ' properties="cover-image"' if cover_property else ""
    meta = f'    <meta name="cover" content="{cover_meta}"/>\n' if cover_meta else ""
    return f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="id" version="2.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Test</dc:title>
{meta}  </metadata>
  <manifest>
    <item id="cover-img" href="cover.jpg" media-type="image/jpeg"{props}/>
    <item id="titlepage" href="titlepage.xhtml" media-type="application/xhtml+xml"/>
    <item id="c1" href="chapter.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="titlepage"/>
    <itemref idref="c1"/>
  </spine>
  <guide>
    <reference type="cover" href="titlepage.xhtml" title="Cover"/>
  </guide>
</package>
"""


def _write_epub(path: Path, opf: str) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", CONTAINER)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/cover.jpg", b"cover-bytes")
        archive.writestr("OEBPS/titlepage.xhtml", TITLEPAGE)
        archive.writestr("OEBPS/chapter.xhtml", CHAPTER)


def _read_opf(path: Path) -> str:
    with zipfile.ZipFile(path, "r") as archive:
        return archive.read("OEBPS/content.opf").decode("utf-8")


def test_convert_epub2_keeps_shebang() -> None:
    first = (PROJECT_ROOT / "convert_epub2.py").read_text().splitlines()[0]
    assert first == "#!/usr/bin/env python3"


def _assert_valid_cover_item(opf: str, item_id: str = "cover-img") -> None:
    import xml.etree.ElementTree as ET

    root = ET.fromstring(opf)
    item = None
    for el in root.iter():
        if el.tag.endswith("item") and el.get("id") == item_id:
            item = el
            break
    assert item is not None
    assert "cover-image" in (item.get("properties") or "").split()
    assert "/ properties=" not in opf


def test_mark_cover_adds_meta_and_cover_image_property(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    _write_epub(epub, _opf(cover_meta=None, cover_property=False))

    assert convert_epub2.mark_cover_for_thumbnailers(epub)

    opf = _read_opf(epub)
    assert 'name="cover"' in opf
    assert 'content="cover-img"' in opf
    _assert_valid_cover_item(opf)
    with zipfile.ZipFile(epub) as archive:
        assert archive.namelist()[0] == "mimetype"
        assert archive.read("mimetype") == b"application/epub+zip"
        assert archive.getinfo("mimetype").compress_type == zipfile.ZIP_STORED


def test_mark_cover_follows_titlepage_meta(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    _write_epub(epub, _opf(cover_meta="titlepage", cover_property=False))

    assert convert_epub2.mark_cover_for_thumbnailers(epub)

    opf = _read_opf(epub)
    assert 'content="cover-img"' in opf
    _assert_valid_cover_item(opf)


def test_repair_broken_cover_item_from_old_rewriter() -> None:
    broken = (
        '<item id="cover" href="cover.jpeg" media-type="image/jpeg"/'
        ' properties="cover-image">'
    )
    fixed = convert_epub2._repair_broken_cover_item(broken)
    assert '/ properties=' not in fixed
    assert fixed.endswith('properties="cover-image"/>')


def test_mark_cover_keeps_self_closing_item_well_formed() -> None:
    opf = (
        '<package xmlns="http://www.idpf.org/2007/opf" version="2.0">\n'
        "  <metadata>\n"
        '    <meta name="cover" content="cover"/>\n'
        "  </metadata>\n"
        "  <manifest>\n"
        '    <item id="cover" href="cover.jpeg" media-type="image/jpeg"/>\n'
        "  </manifest>\n"
        "</package>\n"
    )
    patched = convert_epub2._ensure_cover_image_property(opf, "cover")
    assert "/ properties=" not in patched
    assert 'media-type="image/jpeg" properties="cover-image"/>' in patched
    import xml.etree.ElementTree as ET

    ET.fromstring(patched)


def test_mark_cover_is_idempotent(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    _write_epub(epub, _opf(cover_meta="cover-img", cover_property=True))

    assert convert_epub2.mark_cover_for_thumbnailers(epub)
    assert convert_epub2.mark_cover_for_thumbnailers(epub)

    opf = _read_opf(epub)
    assert opf.count("cover-image") == 1
    assert opf.count('name="cover"') == 1


def test_convert_in_place_marks_cover_before_replace(tmp_path: Path) -> None:
    epub = tmp_path / "book.epub"
    _write_epub(epub, _opf(cover_meta=None, cover_property=False))
    converted = tmp_path / "converted.epub"
    _write_epub(converted, _opf(cover_meta="titlepage", cover_property=False))

    def fake_run(args, **_kwargs):
        cmd = args[0]
        if "ebook-meta" in cmd:
            dest = Path(args[args.index("--get-cover") + 1])
            dest.write_bytes(b"extracted-cover")
            return MagicMock(returncode=0, stdout="", stderr="")
        dest = Path(args[2])
        dest.write_bytes(converted.read_bytes())
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch.object(convert_epub2.subprocess, "run", side_effect=fake_run):
        assert convert_epub2.convert_in_place(
            epub,
            convert_cmd="/usr/bin/ebook-convert",
            meta_cmd="/usr/bin/ebook-meta",
            dry_run=False,
            verbose=False,
        )

    opf = _read_opf(epub)
    assert 'content="cover-img"' in opf
    _assert_valid_cover_item(opf)
    with zipfile.ZipFile(epub) as archive:
        assert archive.namelist()[0] == "mimetype"
