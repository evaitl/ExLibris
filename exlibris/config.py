from pathlib import Path

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_BOOKS_DIR = Path("/media/books")
DEFAULT_DATABASE_PATH = DATA_DIR / "library.db"
DEFAULT_COVERS_DIR = DATA_DIR / "covers"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EXLIBRIS_",
        env_file=".env",
        extra="ignore",
    )

    scan_paths: list[Path] = Field(default_factory=lambda: [Path("/media/books")])
    database_path: Path = Path("data/library.db")
    covers_dir: Path = Path("data/covers")

    @classmethod
    def from_yaml(cls, path: Path) -> "Settings":
        data: dict = {}
        if path.exists():
            with path.open(encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
            if not isinstance(loaded, dict):
                raise ValueError(f"Config file {path} must contain a YAML mapping")
            data = loaded
        return cls(**data)


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
    if config:
        return Settings.from_yaml(config.expanduser())
    default = PROJECT_ROOT / "config.yaml"
    if default.exists():
        return Settings.from_yaml(default)
    return Settings()
