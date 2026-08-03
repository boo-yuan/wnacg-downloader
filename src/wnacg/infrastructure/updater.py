"""Authenticated update metadata checks against the official GitHub API."""

import json
from importlib.metadata import PackageNotFoundError, version
from urllib.parse import urlsplit

from curl_cffi.requests import AsyncSession, Response
from packaging.version import Version
from pydantic import BaseModel, ConfigDict, Field

from wnacg.infrastructure.config import ProxyMode, cfg


class GitHubRelease(BaseModel):
    """Validated subset of GitHub's latest-release response."""

    model_config = ConfigDict(extra="ignore")

    tag_name: str = Field(min_length=1, max_length=128)
    body: str = Field(default="", max_length=200_000)
    html_url: str = Field(min_length=1, max_length=4_096)


class UpdateResult(BaseModel):
    """Safe update information consumed by the UI."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    has_update: bool
    current_version: str
    latest_version: str
    release_notes: str
    download_url: str


class UpdateCheckError(RuntimeError):
    """Raised when update status cannot be determined."""


class Updater:
    """Check official release metadata without downloading executable code."""

    REPOSITORY = "boo-yuan/wnacg-downloader"
    API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
    RELEASE_PATH_PREFIX = f"/{REPOSITORY}/releases/"

    @staticmethod
    def current_version() -> str:
        """Read the installed project version from package metadata."""
        try:
            return version("wnacg-downloader")
        except PackageNotFoundError as error:
            raise UpdateCheckError("Package metadata is unavailable") from error

    @classmethod
    async def check_update(cls) -> UpdateResult:
        """Return validated metadata from the official GitHub release page."""
        match cfg.proxy_mode:
            case ProxyMode.CUSTOM:
                session = AsyncSession[Response](
                    impersonate="chrome",
                    timeout=15.0,
                    proxy=cfg.custom_proxy,
                )
            case ProxyMode.DIRECT:
                session = AsyncSession[Response](impersonate="chrome", timeout=15.0, trust_env=False)
            case ProxyMode.SYSTEM:
                session = AsyncSession[Response](impersonate="chrome", timeout=15.0, trust_env=True)

        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        try:
            async with session as client:
                response: Response = await client.get(cls.API_URL, headers=headers)
                response.raise_for_status()
                release_payload: object = json.loads(response.text)
                release = GitHubRelease.model_validate(release_payload)
        except Exception as error:
            raise UpdateCheckError(f"GitHub update check failed: {error}") from error

        release_url = urlsplit(release.html_url)
        if (
            release_url.scheme != "https"
            or release_url.hostname != "github.com"
            or not release_url.path.startswith(cls.RELEASE_PATH_PREFIX)
        ):
            raise UpdateCheckError("GitHub returned an unexpected release URL")

        current_version = cls.current_version()
        latest_version = release.tag_name.removeprefix("v")
        try:
            has_update = Version(latest_version) > Version(current_version)
        except ValueError as error:
            raise UpdateCheckError(f"Invalid release version: {release.tag_name}") from error

        return UpdateResult(
            has_update=has_update,
            current_version=current_version,
            latest_version=release.tag_name,
            release_notes=release.body,
            download_url=release.html_url,
        )
