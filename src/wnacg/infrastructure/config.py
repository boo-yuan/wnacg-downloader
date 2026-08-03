"""Validated application configuration persisted as atomic JSON."""

import json
import os
import shutil
import threading
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlsplit

from loguru import logger
from pydantic import Field, PrivateAttr, TypeAdapter, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from wnacg.domain.models import DownloadFormat, DownloadNaming
from wnacg.infrastructure.network_safety import validate_public_https_url
from wnacg.infrastructure.paths import DATA_DIR

CONFIG_FILE = DATA_DIR / "config.json"


class ProxyMode(StrEnum):
    """Network proxy selection."""

    DIRECT = "direct"
    SYSTEM = "system"
    CUSTOM = "custom"


class AppearanceTheme(StrEnum):
    """Persisted application appearance preference."""

    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"


class AppConfig(BaseSettings):
    """Strict settings loaded from JSON and optional ``WNACG_`` environment values."""

    model_config = SettingsConfigDict(
        env_prefix="WNACG_",
        extra="forbid",
        validate_assignment=True,
    )
    _environment_fields: set[str] = PrivateAttr(default_factory=set)

    proxy_mode: ProxyMode = ProxyMode.SYSTEM
    appearance_theme: AppearanceTheme = AppearanceTheme.SYSTEM
    custom_proxy: str = "http://127.0.0.1:7890"
    download_dir: str = str(Path.home() / "Downloads" / "wnacg")
    domain: str = "www.wnacg.com"
    backup_domains: list[str] = Field(default_factory=lambda: ["www.wnacg.com", "www.wnacg.ru"])
    download_naming: DownloadNaming = DownloadNaming.ORIGINAL
    download_format: DownloadFormat = DownloadFormat.JPG
    auto_start_download: bool = True
    max_concurrent_tasks: int = Field(default=2, ge=1, le=10)
    global_max_connections: int = Field(default=8, ge=1, le=64)
    download_delay: float = Field(default=1.0, ge=0.0, le=60.0)
    pack_to_zip: bool = False
    delete_original_after_pack: bool = False
    close_to_tray: bool = True
    show_close_prompt: bool = True
    show_cancel_prompt: bool = True
    delete_files_on_cancel: bool = False
    global_speed_limit: int = Field(default=0, ge=0, le=10_000_000)
    max_image_bytes: int = Field(default=100 * 1024 * 1024, ge=1024 * 1024, le=1024 * 1024 * 1024)
    max_cover_bytes: int = Field(default=10 * 1024 * 1024, ge=256 * 1024, le=100 * 1024 * 1024)
    max_html_bytes: int = Field(default=4 * 1024 * 1024, ge=256 * 1024, le=32 * 1024 * 1024)
    max_gallery_images: int = Field(default=5_000, ge=1, le=50_000)
    max_gallery_pages: int = Field(default=500, ge=1, le=5_000)
    max_task_bytes: int = Field(default=20 * 1024 * 1024 * 1024, ge=100 * 1024 * 1024)
    minimum_free_space_bytes: int = Field(default=512 * 1024 * 1024, ge=64 * 1024 * 1024)
    max_image_pixels: int = Field(default=100_000_000, ge=1_000_000, le=500_000_000)

    @field_validator("download_dir")
    @classmethod
    def validate_download_dir(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("download_dir cannot be empty")
        path = Path(value).expanduser()
        return str(path)

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, value: str) -> str:
        normalized = value.strip().lower().rstrip(".")
        parsed = urlsplit(f"//{normalized}")
        if (
            not normalized
            or parsed.hostname != normalized
            or parsed.port is not None
            or "/" in normalized
            or "@" in normalized
        ):
            raise ValueError("domain must be a hostname without scheme, path, credentials, or port")
        validate_public_https_url(f"https://{normalized}")
        return normalized

    @field_validator("backup_domains")
    @classmethod
    def validate_backup_domains(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            domain = cls.validate_domain(value)
            if domain not in normalized:
                normalized.append(domain)
        if not normalized:
            raise ValueError("at least one backup domain is required")
        return normalized

    @field_validator("custom_proxy")
    @classmethod
    def validate_custom_proxy(cls, value: str) -> str:
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"http", "https", "socks4", "socks5"} or parsed.hostname is None:
            raise ValueError("custom_proxy must be an HTTP(S) or SOCKS proxy URL")
        return value.strip()

    @model_validator(mode="after")
    def include_primary_domain(self) -> "AppConfig":
        if self.domain not in self.backup_domains:
            self.backup_domains.insert(0, self.domain)
        return self

    @property
    def curl_cffi_proxies(self) -> dict[str, str] | None:
        if self.proxy_mode is ProxyMode.CUSTOM:
            return {"http": self.custom_proxy, "https": self.custom_proxy}
        return None

    def save(self) -> None:
        """Persist settings with same-directory atomic replacement."""
        with _CONFIG_WRITE_LOCK:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            persisted_data = self.model_dump()
            if self._environment_fields and CONFIG_FILE.is_file():
                try:
                    existing_data = TypeAdapter(dict[str, object]).validate_python(
                        json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                    )
                except Exception:
                    existing_data = {}
                defaults = AppConfig.model_validate({}).model_dump()
                for field_name in self._environment_fields:
                    persisted_data[field_name] = existing_data.get(field_name, defaults[field_name])
            persisted = AppConfig.model_validate(persisted_data)
            temporary_file = CONFIG_FILE.with_name(f".{CONFIG_FILE.name}.{uuid.uuid4().hex}.tmp")
            try:
                with temporary_file.open("w", encoding="utf-8", newline="\n") as output:
                    output.write(persisted.model_dump_json(indent=4))
                    output.flush()
                    os.fsync(output.fileno())
                temporary_file.replace(CONFIG_FILE)
            finally:
                temporary_file.unlink(missing_ok=True)


def _backup_invalid_config() -> None:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_file = CONFIG_FILE.with_name(f"config.invalid-{timestamp}.json")
    shutil.copy2(CONFIG_FILE, backup_file)


def _recover_valid_fields(data: dict[str, object]) -> AppConfig:
    recovered: dict[str, object] = {}
    for field_name in AppConfig.model_fields:
        if field_name not in data:
            continue
        try:
            candidate = AppConfig.model_validate({field_name: data[field_name]})
        except Exception as error:
            logger.warning("Ignoring invalid config field", field=field_name, error=str(error))
            continue
        recovered[field_name] = getattr(candidate, field_name)
    return AppConfig.model_validate(recovered)


def _validate_with_environment(data: dict[str, object]) -> AppConfig:
    """Validate persisted values while giving explicit environment settings precedence."""
    environment = AppConfig()
    overrides = environment.model_dump(include=environment.model_fields_set)
    validated = AppConfig.model_validate({**data, **overrides})
    validated._environment_fields = set(environment.model_fields_set)
    return validated


def load_config() -> AppConfig:
    """Load settings while preserving valid fields and backing up corrupt JSON."""
    if not CONFIG_FILE.exists():
        persisted = AppConfig.model_validate({})
        persisted.save()
        return _validate_with_environment(persisted.model_dump())

    try:
        loaded_data = TypeAdapter(dict[str, object]).validate_python(
            json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        )
    except Exception as error:
        _backup_invalid_config()
        logger.error("Config JSON is invalid; backup created", error=str(error))
        persisted = AppConfig.model_validate({})
        persisted.save()
        return _validate_with_environment(persisted.model_dump())

    try:
        persisted = AppConfig.model_validate(loaded_data)
    except Exception as error:
        logger.warning("Recovering valid config fields", error=str(error))
        persisted = _recover_valid_fields(loaded_data)
    persisted.save()
    return _validate_with_environment(persisted.model_dump())


_CONFIG_WRITE_LOCK = threading.RLock()
cfg = AppConfig()


def initialize_config() -> AppConfig:
    """Load persisted settings into the stable application configuration object."""
    loaded = load_config()
    for field_name in AppConfig.model_fields:
        setattr(cfg, field_name, getattr(loaded, field_name))
    cfg._environment_fields = set(loaded._environment_fields)
    return cfg
