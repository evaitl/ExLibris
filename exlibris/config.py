from pathlib import Path

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BOOKS_DIR = PROJECT_ROOT / "books"
DEFAULT_COVERS_DIR = PROJECT_ROOT / "covers"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EXLIBRIS_",
        env_file=".env",
        extra="ignore",
    )

    scan_paths: list[Path] = Field(default_factory=lambda: [Path("books")])
    database_path: Path = Path("library.db")
    covers_dir: Path = Path("covers")
    host: str = "127.0.0.1"
    port: int = 8080

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


def resolve_covers_dir(path: Path | None = None) -> Path:
    covers = Path(path) if path else DEFAULT_COVERS_DIR
    if not covers.is_absolute():
        covers = PROJECT_ROOT / covers
    covers.mkdir(parents=True, exist_ok=True)
    return covers.resolve()


def load_settings(config: Path | None = None) -> Settings:
    if config:
        return Settings.from_yaml(config.expanduser())
    default = PROJECT_ROOT / "config.yaml"
    if default.exists():
        return Settings.from_yaml(default)
    return Settings()
