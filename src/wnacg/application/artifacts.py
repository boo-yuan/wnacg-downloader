"""Manifest-based reconciliation and atomic archive creation for task artifacts."""

import contextlib
import hashlib
import re
import uuid
import zipfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from wnacg.application.file_paths import archive_path

_MANIFEST_NAME = ".wnacg-manifest.json"
_PARTIAL_DOWNLOAD_NAME = re.compile(r"^\.\d{4}\.[0-9a-f]{32}\.download$")


class ArtifactManifest(BaseModel):
    """Validated record of files exclusively managed by one download task."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(default=1, ge=1, le=1)
    task_id: str = Field(min_length=1, max_length=128)
    files: list[str] = Field(default_factory=list, max_length=50_000)
    archive_created: bool = False

    @field_validator("files")
    @classmethod
    def validate_file_names(cls, values: list[str]) -> list[str]:
        """Allow only unique direct-child file names in a task manifest."""
        unique: list[str] = []
        for value in values:
            candidate = Path(value)
            if candidate.name != value or value in {"", ".", "..", _MANIFEST_NAME}:
                raise ValueError(f"Unsafe artifact name: {value}")
            if value not in unique:
                unique.append(value)
        return unique


def _manifest_path(metadata_directory: Path, task_id: str) -> Path:
    task_key = hashlib.sha256(task_id.encode("utf-8")).hexdigest()
    return metadata_directory / f"{task_key}.json"


def _legacy_manifest_path(source_directory: Path) -> Path:
    return source_directory / _MANIFEST_NAME


def load_manifest(source_directory: Path, task_id: str, metadata_directory: Path) -> ArtifactManifest | None:
    """Load external or legacy ownership metadata, treating invalid files as unowned."""
    for manifest_path in (
        _manifest_path(metadata_directory, task_id),
        _legacy_manifest_path(source_directory),
    ):
        if not manifest_path.is_file() or manifest_path.is_symlink():
            continue
        try:
            manifest = ArtifactManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if manifest.task_id == task_id:
            return manifest
    return None


def _validated_managed_file(source_directory: Path, name: str) -> Path:
    candidate = source_directory / name
    if candidate.parent != source_directory or candidate.is_symlink():
        raise ValueError(f"Unsafe managed artifact: {candidate}")
    return candidate


def _write_manifest(metadata_directory: Path, manifest: ArtifactManifest) -> None:
    metadata_directory.mkdir(parents=True, exist_ok=True)
    manifest_path = _manifest_path(metadata_directory, manifest.task_id)
    temporary_path = manifest_path.with_name(f".{manifest_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        temporary_path.replace(manifest_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def migrate_manifest(source_directory: Path, task_id: str, metadata_directory: Path) -> None:
    """Move legacy in-gallery ownership metadata to the application data directory."""
    legacy_path = _legacy_manifest_path(source_directory)
    manifest = load_manifest(source_directory, task_id, metadata_directory)
    if manifest is None:
        return
    _write_manifest(metadata_directory, manifest)
    legacy_path.unlink(missing_ok=True)


def forget_manifest(source_directory: Path, task_id: str, metadata_directory: Path) -> None:
    """Remove ownership metadata without touching preserved user-visible artifacts."""
    _manifest_path(metadata_directory, task_id).unlink(missing_ok=True)
    _legacy_manifest_path(source_directory).unlink(missing_ok=True)


def create_archive(source_directory: Path, managed_files: list[Path]) -> Path:
    """Atomically archive only validated, regular task files."""
    final_archive = archive_path(source_directory)
    temporary_archive = final_archive.with_name(f".{final_archive.name}.{uuid.uuid4().hex}.tmp")
    try:
        with zipfile.ZipFile(temporary_archive, "w", zipfile.ZIP_DEFLATED) as archive:
            for managed_file in managed_files:
                validated = _validated_managed_file(source_directory, managed_file.name)
                if not validated.is_file():
                    raise FileNotFoundError(validated)
                archive.write(validated, validated.name)
        with zipfile.ZipFile(temporary_archive) as archive:
            corrupt_member = archive.testzip()
            if corrupt_member is not None:
                raise ValueError(f"Corrupt ZIP member: {corrupt_member}")
        temporary_archive.replace(final_archive)
        return final_archive
    finally:
        temporary_archive.unlink(missing_ok=True)


def reconcile_artifacts(
    *,
    task_id: str,
    source_directory: Path,
    current_files: list[Path],
    pack_to_zip: bool,
    delete_originals: bool,
    metadata_directory: Path,
) -> None:
    """Remove previously owned stale files and publish the current manifest/archive."""
    current_names = {path.name for path in current_files}
    previous = load_manifest(source_directory, task_id, metadata_directory)
    if previous is not None:
        for stale_name in set(previous.files) - current_names:
            stale_file = _validated_managed_file(source_directory, stale_name)
            if stale_file.is_file():
                stale_file.unlink()

    final_archive = archive_path(source_directory)
    if pack_to_zip:
        create_archive(source_directory, current_files)
    elif (
        previous is not None and previous.archive_created and final_archive.is_file() and not final_archive.is_symlink()
    ):
        final_archive.unlink()

    manifest = ArtifactManifest(
        task_id=task_id,
        files=sorted(current_names),
        archive_created=pack_to_zip,
    )
    _write_manifest(metadata_directory, manifest)
    _legacy_manifest_path(source_directory).unlink(missing_ok=True)

    if pack_to_zip and delete_originals:
        for current_file in current_files:
            validated = _validated_managed_file(source_directory, current_file.name)
            if validated.is_file():
                validated.unlink()
        with contextlib.suppress(OSError):
            source_directory.rmdir()


def remove_owned_artifacts(
    *,
    task_id: str,
    source_directory: Path,
    expected_files: list[Path],
    metadata_directory: Path,
) -> None:
    """Remove only files attributable to a task and preserve unrelated content."""
    owned_names = {path.name for path in expected_files if path.parent == source_directory}
    previous = load_manifest(source_directory, task_id, metadata_directory)
    if previous is not None:
        owned_names.update(previous.files)

    if source_directory.is_dir():
        for candidate in source_directory.iterdir():
            if _PARTIAL_DOWNLOAD_NAME.fullmatch(candidate.name):
                owned_names.add(candidate.name)

    for owned_name in owned_names:
        candidate = _validated_managed_file(source_directory, owned_name)
        if candidate.is_file():
            candidate.unlink()

    if previous is not None and previous.archive_created:
        final_archive = archive_path(source_directory)
        if final_archive.is_symlink():
            raise ValueError(f"Unsafe task archive is a filesystem link: {final_archive}")
        if final_archive.is_file():
            final_archive.unlink()

    forget_manifest(source_directory, task_id, metadata_directory)
    # The directory may contain user-owned or otherwise unrecognized content.
    with contextlib.suppress(OSError):
        source_directory.rmdir()
