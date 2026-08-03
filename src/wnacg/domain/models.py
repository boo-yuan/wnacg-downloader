"""Validated domain models and value objects."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator


class TaskStatus(StrEnum):
    """Lifecycle states for a download task."""

    PENDING = "pending"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    MISSING = "missing"
    CANCELED = "canceled"


CANCELLABLE_TASK_STATUSES = frozenset(
    {
        TaskStatus.PENDING,
        TaskStatus.DOWNLOADING,
        TaskStatus.PAUSED,
        TaskStatus.FAILED,
        TaskStatus.MISSING,
    }
)


class DownloadNaming(StrEnum):
    """Supported image naming policies."""

    ORIGINAL = "original"
    SEQUENTIAL = "sequential"


class DownloadFormat(StrEnum):
    """Supported output image formats."""

    ORIGINAL = "original"
    JPG = "jpg"
    PNG = "png"
    WEBP = "webp"


class DownloadOptions(BaseModel):
    """Immutable settings snapshot used for one complete task."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    naming: DownloadNaming = DownloadNaming.ORIGINAL
    image_format: DownloadFormat = DownloadFormat.JPG
    delay_seconds: float = Field(default=1.0, ge=0.0, le=60.0)
    pack_to_zip: bool = False
    delete_original_after_pack: bool = False
    naming_version: int = Field(default=2, ge=1, le=2)


class Comic(BaseModel):
    """Gallery metadata required by search and download use cases."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    aid: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=1_000)
    cover_url: str = Field(default="", max_length=4_096)
    url: str = Field(default="", max_length=4_096)
    pic_count: str = Field(default="", max_length=64)
    date: str = Field(default="", max_length=64)

    @field_validator("aid")
    @classmethod
    def validate_aid(cls, value: str) -> str:
        """Reject identifiers that cannot safely identify a gallery."""
        if not value.replace("-", "").replace("_", "").isalnum():
            raise ValueError("aid must contain only letters, numbers, '-' or '_'")
        return value


class DownloadTask(BaseModel):
    """Persisted aggregate state for one gallery download."""

    model_config = ConfigDict(validate_assignment=True, extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    comic: Comic
    status: TaskStatus = TaskStatus.PENDING
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    total_images: int = Field(default=0, ge=0)
    downloaded_images: int = Field(default=0, ge=0)
    save_path: str = ""
    download_root: str = ""
    options: DownloadOptions | None = None
    error_message: str | None = Field(default=None, max_length=4_096)

    @field_validator("total_images")
    @classmethod
    def validate_total_count(cls, value: int, info: ValidationInfo) -> int:
        """Reject a total that would invalidate an existing downloaded count."""
        downloaded = int(info.data.get("downloaded_images", 0))
        if downloaded > value:
            raise ValueError("downloaded_images cannot exceed total_images")
        return value

    @field_validator("downloaded_images")
    @classmethod
    def validate_downloaded_count(cls, value: int, info: ValidationInfo) -> int:
        """Keep persisted progress within the task's declared image count."""
        total = int(info.data.get("total_images", 0))
        if value > total:
            raise ValueError("downloaded_images cannot exceed total_images")
        return value

    @model_validator(mode="after")
    def validate_progress_aggregate(self) -> "DownloadTask":
        """Keep all progress fields mutually consistent after construction."""
        if self.downloaded_images > self.total_images:
            raise ValueError("downloaded_images cannot exceed total_images")
        expected_progress = self.downloaded_images / self.total_images if self.total_images else 0.0
        if abs(self.progress - expected_progress) > 1e-9:
            raise ValueError("progress must match downloaded_images / total_images")
        return self

    def set_progress(self, downloaded_images: int, total_images: int) -> None:
        """Validate and replace aggregate progress as one logical operation."""
        progress = downloaded_images / total_images if total_images else 0.0
        validated = type(self).model_validate(
            {
                **self.model_dump(),
                "downloaded_images": downloaded_images,
                "total_images": total_images,
                "progress": progress,
            }
        )
        object.__setattr__(self, "downloaded_images", validated.downloaded_images)
        object.__setattr__(self, "total_images", validated.total_images)
        object.__setattr__(self, "progress", validated.progress)


_ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset({TaskStatus.DOWNLOADING, TaskStatus.PAUSED, TaskStatus.CANCELED}),
    TaskStatus.DOWNLOADING: frozenset(
        {TaskStatus.PAUSED, TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELED}
    ),
    TaskStatus.PAUSED: frozenset({TaskStatus.PENDING, TaskStatus.CANCELED}),
    TaskStatus.FAILED: frozenset({TaskStatus.PENDING, TaskStatus.CANCELED}),
    TaskStatus.COMPLETED: frozenset({TaskStatus.PENDING, TaskStatus.MISSING}),
    TaskStatus.MISSING: frozenset({TaskStatus.PENDING, TaskStatus.CANCELED}),
    TaskStatus.CANCELED: frozenset(),
}


def validate_status_transition(current: TaskStatus, target: TaskStatus) -> None:
    """Reject impossible task lifecycle transitions."""
    if current != target and target not in _ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"Invalid task status transition: {current.value} -> {target.value}")
