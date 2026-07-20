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
DEFAULT_WEB_HOST = "127.0.0.1"
DEFAULT_WEB_PORT = 8080


@dataclass
class Settings:
    scan_paths: list[Path] = field(default_factory=lambda: [DEFAULT_BOOKS_DIR])
    database_path: Path = field(default_factory=lambda: Path("data/library.db"))
    covers_dir: Path = field(default_factory=lambda: Path("data/covers"))
    web_host: str = DEFAULT_WEB_HOST
    web_port: int = DEFAULT_WEB_PORT


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


def _coerce_web_port(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("web_port must be an integer")
    try:
        port = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError("web_port must be an integer") from exc
    if not (1 <= port <= 65535):
        raise ValueError("web_port must be between 1 and 65535")
    return port


def settings_from_dict(data: dict) -> Settings:
    scan_paths = data.get("scan_paths", [str(DEFAULT_BOOKS_DIR)])
    if not isinstance(scan_paths, list):
        raise ValueError("scan_paths must be a list")
    web_host = data.get("web_host", DEFAULT_WEB_HOST)
    if not isinstance(web_host, str) or not web_host.strip():
        raise ValueError("web_host must be a non-empty string")
    return Settings(
        scan_paths=[_coerce_path(path) for path in scan_paths],
        database_path=_coerce_path(data.get("database_path", "data/library.db")),
        covers_dir=_coerce_path(data.get("covers_dir", "data/covers")),
        web_host=web_host.strip(),
        web_port=_coerce_web_port(data.get("web_port", DEFAULT_WEB_PORT)),
    )


def _apply_env(settings: Settings) -> Settings:
    database_path = settings.database_path
    covers_dir = settings.covers_dir
    scan_paths = list(settings.scan_paths)
    web_host = settings.web_host
    web_port = settings.web_port

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

    env_host = os.environ.get("EXLIBRIS_WEB_HOST")
    if env_host and env_host.strip():
        web_host = env_host.strip()

    env_port = os.environ.get("EXLIBRIS_WEB_PORT")
    if env_port:
        web_port = _coerce_web_port(env_port)

    return Settings(
        scan_paths=scan_paths,
        database_path=database_path,
        covers_dir=covers_dir,
        web_host=web_host,
        web_port=web_port,
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
