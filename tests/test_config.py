"""Tests for JSON configuration loading."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from exlibris.config import (
    load_config_dict,
    load_settings,
    resolve_database_path,
    resolve_scan_path,
    settings_from_dict,
)


def test_load_config_dict_reads_json() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.json"
        path.write_text(
            json.dumps(
                {
                    "scan_paths": ["/books"],
                    "database_path": "data/test.db",
                    "covers_dir": "data/covers",
                }
            ),
            encoding="utf-8",
        )
        data = load_config_dict(path)
        assert data["scan_paths"] == ["/books"]


def test_load_settings_applies_env_overrides(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.json"
        path.write_text(
            json.dumps(
                {
                    "scan_paths": ["/books"],
                    "database_path": "data/test.db",
                    "covers_dir": "data/covers",
                    "web_host": "127.0.0.1",
                    "web_port": 8080,
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("EXLIBRIS_DATABASE_PATH", "/tmp/custom.db")
        monkeypatch.setenv("EXLIBRIS_SCAN_PATHS", "/one:/two")
        monkeypatch.setenv("EXLIBRIS_WEB_HOST", "0.0.0.0")
        monkeypatch.setenv("EXLIBRIS_WEB_PORT", "9090")
        settings = load_settings(path)
        assert settings.database_path == Path("/tmp/custom.db")
        assert [str(path) for path in settings.scan_paths] == ["/one", "/two"]
        assert settings.web_host == "0.0.0.0"
        assert settings.web_port == 9090


def test_settings_from_dict_reads_web_bind() -> None:
    settings = settings_from_dict(
        {
            "scan_paths": ["/books"],
            "web_host": "0.0.0.0",
            "web_port": 3000,
        }
    )
    assert settings.web_host == "0.0.0.0"
    assert settings.web_port == 3000


def test_settings_from_dict_resolves_relative_scan_paths() -> None:
    settings = settings_from_dict({"scan_paths": ["books"]})
    resolved = resolve_scan_path(settings.scan_paths[0])
    assert resolved.is_absolute()


def test_resolve_database_path_creates_parent() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = resolve_database_path(Path(tmp) / "nested" / "library.db")
        assert db.parent.is_dir()
