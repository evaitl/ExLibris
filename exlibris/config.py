from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_BOOKS_DIR = Path("/media/books")
DEFAULT_DATABASE_PATH = DATA_DIR / "library.db"
DEFAULT_COVERS_DIR = DATA_DIR / "covers"
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.json"


@dataclass
class Settings:
    scan_paths: list[Path] = field(default_factory=lambda: [DEFAULT_BOOKS_DIR])
    database_path: Path = field(default_factory=lambda: Path("data/library.db"))
    covers_dir: Path = field(default_factory=lambda: Path("data/covers"))


def default_config_path() -> Path:
    return DEFAULT_CONFIG_PATH


def load_config_dict(config: Path | None = None) -> dict:
    """Load settings from a JSON config file. Returns {} when the file is missing."""
    path = config.expanduser() if config else default_config_path()
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Config file {path} must contain a JSON object")
    return data


def _coerce_path(value: object) -> Path:
    if isinstance(value, Path):
        return value
    return Path(str(value))


def settings_from_dict(data: dict) -> Settings:
    scan_paths = data.get("scan_paths", [str(DEFAULT_BOOKS_DIR)])
    if not isinstance(scan_paths, list):
        raise ValueError("scan_paths must be a list")
    return Settings(
        scan_paths=[_coerce_path(path) for path in scan_paths],
        database_path=_coerce_path(data.get("database_path", "data/library.db")),
        covers_dir=_coerce_path(data.get("covers_dir", "data/covers")),
    )


def _apply_env(settings: Settings) -> Settings:
    database_path = settings.database_path
    covers_dir = settings.covers_dir
    scan_paths = list(settings.scan_paths)

    env_db = os.environ.get("EXLIBRIS_DATABASE_PATH")
    if env_db:
        database_path = Path(env_db).expanduser()

    env_covers = os.environ.get("EXLIBRIS_COVERS_DIR")
    if env_covers:
        covers_dir = Path(env_covers).expanduser()

    env_scan = os.environ.get("EXLIBRIS_SCAN_PATHS")
    if env_scan:
        scan_paths = [
            Path(part.strip()).expanduser()
            for part in env_scan.split(os.pathsep)
            if part.strip()
        ]

    return Settings(
        scan_paths=scan_paths,
        database_path=database_path,
        covers_dir=covers_dir,
    )


def resolve_scan_path(path: Path) -> Path:
    path = Path(path).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def resolve_project_path(path: Path) -> Path:
    path = Path(path).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def resolve_database_path(path: Path | None = None) -> Path:
    db = resolve_project_path(path) if path else DEFAULT_DATABASE_PATH
    db.parent.mkdir(parents=True, exist_ok=True)
    return db


def resolve_covers_dir(path: Path | None = None) -> Path:
    covers = resolve_project_path(path) if path else DEFAULT_COVERS_DIR
    covers.mkdir(parents=True, exist_ok=True)
    return covers


def library_books_dirs() -> list[Path]:
    """Resolved directories that may contain library ebook files."""
    settings = load_settings()
    return [resolve_scan_path(path) for path in settings.scan_paths]


def load_settings(config: Path | None = None) -> Settings:
    data = load_config_dict(config)
    settings = settings_from_dict(data) if data else Settings()
    return _apply_env(settings)
