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
        return Path.home() / "AppData" / "Local" / "wnacg-downloader"
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


def _configured_data_dir() -> Path:
    """Resolve the configured data path without touching the filesystem."""
    settings = DataDirectorySettings()
    return (settings.data_dir or _default_data_dir()).expanduser().resolve()


def initialize_data_dir(data_dir: Path) -> tuple[str, ...]:
    """Create the configured data directory and non-destructively copy legacy data."""
    data_dir.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    application_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parents[3]
    migration_marker = data_dir / ".legacy-migration-v1.complete"
    if not migration_marker.exists():
        _copy_missing(Path(f"{data_dir}_bak"), data_dir, warnings)
        _copy_missing(application_dir / "data", data_dir, warnings)

        old_log = application_dir / "app.log"
        current_log = data_dir / "app.jsonl"
        if old_log.is_file() and not current_log.exists():
            try:
                shutil.copy2(old_log, data_dir / "legacy-app.log")
            except OSError as error:
                warnings.append(f"Legacy log migration failed: {error}")
        if not warnings:
            migration_marker.write_text("completed\n", encoding="utf-8")
    return tuple(warnings)


DATA_DIR = _configured_data_dir()
CACHE_DIR = DATA_DIR / "cache"
ARTIFACT_METADATA_DIR = DATA_DIR / "artifacts"
_path_migration_warnings: tuple[str, ...] = ()


def initialize_paths() -> None:
    """Create application-owned paths and run each legacy migration once."""
    global _path_migration_warnings
    _path_migration_warnings = initialize_data_dir(DATA_DIR)


def path_migration_warnings() -> tuple[str, ...]:
    """Return warnings produced by the explicit startup migration."""
    return _path_migration_warnings
