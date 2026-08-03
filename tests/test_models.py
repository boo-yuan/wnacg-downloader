import pytest
from pydantic import ValidationError

from wnacg.domain.models import Comic, DownloadTask, TaskStatus, validate_status_transition


def test_comic_rejects_path_like_aid() -> None:
    with pytest.raises(ValidationError):
        Comic(aid="../../escape", title="unsafe")


def test_downloaded_images_cannot_exceed_total() -> None:
    comic = Comic(aid="42", title="safe")
    with pytest.raises(ValidationError):
        DownloadTask(id="task", comic=comic, total_images=1, downloaded_images=2)


def test_status_transition_rejects_impossible_jump() -> None:
    with pytest.raises(ValueError):
        validate_status_transition(TaskStatus.PAUSED, TaskStatus.COMPLETED)
    validate_status_transition(TaskStatus.PAUSED, TaskStatus.PENDING)
