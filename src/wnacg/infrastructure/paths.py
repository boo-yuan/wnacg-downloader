"""Application data paths and conservative legacy-data migration."""

import os
import shutil
import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class DataDirectorySettings(BaseSettings):
    """Environment-backed override for packaging and isolated tests."""

    model_config = SettingsConfigDict(env_prefix="WNACG_", extra="ignore")

    data_dir: Path | None = None


def _default_data_dir() -> Path:
    if os.name == "nt":
        appdata_base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        return Path(appdata_base) / "wnacg-downloader" if appdata_base else Path.home() / "wnacg-downloader"
    return Path.home() / ".local" / "share" / "wnacg-downloader"


def _copy_missing(source: Path, destination: Path, warnings: list[str]) -> None:
    """Copy only absent legacy entries, leaving the source as a recoverable backup."""
    if not source.is_dir() or source.resolve(strict=False) == destination.resolve(strict=False):
        return
    try:
        for item in source.iterdir():
            target = destination / item.name
            if target.exists():
                warnings.append(f"Legacy item retained because destination exists: {item}")
            elif item.is_file():
                shutil.copy2(item, target)
            elif item.is_dir():
                shutil.copytree(item, target)
    except OSError as error:
        warnings.append(f"Legacy migration from {source} failed: {error}")


def initialize_data_dir() -> tuple[Path, tuple[str, ...]]:
    """Create the configured data directory and non-destructively copy legacy data."""
    settings = DataDirectorySettings()
    data_dir = (settings.data_dir or _default_data_dir()).expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    application_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parents[3]
    _copy_missing(Path(f"{data_dir}_bak"), data_dir, warnings)
    _copy_missing(application_dir / "data", data_dir, warnings)

    old_log = application_dir / "app.log"
    current_log = data_dir / "app.jsonl"
    if old_log.is_file() and not current_log.exists():
        try:
            shutil.copy2(old_log, data_dir / "legacy-app.log")
        except OSError as error:
            warnings.append(f"Legacy log migration failed: {error}")
    return data_dir, tuple(warnings)


DATA_DIR, PATH_MIGRATION_WARNINGS = initialize_data_dir()
