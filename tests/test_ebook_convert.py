"""Tests for Calibre ebook-convert wrapper."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from exlibris.ebook_convert import EbookConvertError, convert_epub_to_version2


def test_convert_epub_to_version2_invokes_calibre() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "in.epub"
        dest = root / "out.epub"
        source.write_bytes(b"source")

        completed = MagicMock(returncode=0, stdout="", stderr="")
        with patch("exlibris.ebook_convert.subprocess.run", return_value=completed) as run:
            with patch("exlibris.ebook_convert.find_ebook_convert", return_value="/usr/bin/ebook-convert"):
                dest.write_bytes(b"converted")
                convert_epub_to_version2(source, dest)

        run.assert_called_once()
        args = run.call_args.args[0]
        assert args[:3] == ["/usr/bin/ebook-convert", str(source), str(dest)]
        assert "--epub-version=2" in args


def test_convert_epub_to_version2_raises_on_failure() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "in.epub"
        dest = root / "out.epub"
        source.write_bytes(b"source")

        completed = MagicMock(returncode=1, stdout="", stderr="broken")
        with patch("exlibris.ebook_convert.subprocess.run", return_value=completed):
            with patch("exlibris.ebook_convert.find_ebook_convert", return_value="/usr/bin/ebook-convert"):
                with pytest.raises(EbookConvertError, match="broken"):
                    convert_epub_to_version2(source, dest)
