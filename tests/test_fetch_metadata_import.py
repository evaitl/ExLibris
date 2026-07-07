"""Ensure fetch_metadata imports without optional CLI dependencies."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_fetch_metadata_imports_with_stdlib_only_python() -> None:
    root = Path(__file__).resolve().parents[1]
    code = (
        "import sys; "
        f"sys.path.insert(0, {str(root)!r}); "
        "from exlibris.fetch_metadata import enrich_book_from_online; "
        "print('ok')"
    )
    python = Path("/usr/bin/python3")
    if not python.is_file():
        import pytest

        pytest.skip("/usr/bin/python3 not available")
    result = subprocess.run(
        [str(python), "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
