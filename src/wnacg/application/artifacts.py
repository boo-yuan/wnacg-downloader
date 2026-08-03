"""Manifest-based reconciliation and atomic archive creation for task artifacts."""

import contextlib
import uuid
import zipfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from wnacg.application.file_paths import archive_path

_MANIFEST_NAME = ".wnacg-manifest.json"


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


def _manifest_path(source_directory: Path) -> Path:
    return source_directory / _MANIFEST_NAME


def load_manifest(source_directory: Path, task_id: str) -> ArtifactManifest | None:
    """Load a matching manifest, treating invalid or foreign files as unowned."""
    manifest_path = _manifest_path(source_directory)
    if not manifest_path.is_file() or manifest_path.is_symlink():
        return None
    try:
        manifest = ArtifactManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return manifest if manifest.task_id == task_id else None


def _validated_managed_file(source_directory: Path, name: str) -> Path:
    candidate = source_directory / name
    if candidate.parent != source_directory or candidate.is_symlink():
        raise ValueError(f"Unsafe managed artifact: {candidate}")
    return candidate


def _write_manifest(source_directory: Path, manifest: ArtifactManifest) -> None:
    source_directory.mkdir(parents=True, exist_ok=True)
    manifest_path = _manifest_path(source_directory)
    temporary_path = manifest_path.with_name(f".{manifest_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        temporary_path.replace(manifest_path)
    finally:
        temporary_path.unlink(missing_ok=True)


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
) -> None:
    """Remove previously owned stale files and publish the current manifest/archive."""
    current_names = {path.name for path in current_files}
    previous = load_manifest(source_directory, task_id)
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
    _write_manifest(source_directory, manifest)

    if pack_to_zip and delete_originals:
        for current_file in current_files:
            validated = _validated_managed_file(source_directory, current_file.name)
            if validated.is_file():
                validated.unlink()
        _manifest_path(source_directory).unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            # Preserve a directory containing files not owned by this task.
            source_directory.rmdir()
