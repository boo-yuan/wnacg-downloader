import pytest
from pydantic import ValidationError

from wnacg.domain.models import CANCELLABLE_TASK_STATUSES, Comic, DownloadTask, TaskStatus, validate_status_transition


def test_comic_rejects_path_like_aid() -> None:
    with pytest.raises(ValidationError):
        Comic(aid="../../escape", title="unsafe")


def test_downloaded_images_cannot_exceed_total() -> None:
    comic = Comic(aid="42", title="safe")
    with pytest.raises(ValidationError):
        DownloadTask(id="task", comic=comic, total_images=1, downloaded_images=2)


def test_total_images_assignment_cannot_break_downloaded_invariant() -> None:
    comic = Comic(aid="42", title="safe")
    task = DownloadTask(id="task", comic=comic, total_images=2, downloaded_images=2, progress=1.0)

    with pytest.raises(ValidationError):
        task.total_images = 0

    assert task.total_images == 2
    assert task.downloaded_images == 2
    assert task.progress == 1.0


def test_zero_progress_cannot_hide_downloaded_images() -> None:
    comic = Comic(aid="42", title="safe")
    with pytest.raises(ValidationError):
        DownloadTask(id="task", comic=comic, total_images=2, downloaded_images=1, progress=0.0)


def test_progress_is_replaced_atomically() -> None:
    comic = Comic(aid="42", title="safe")
    task = DownloadTask(id="task", comic=comic, total_images=2, downloaded_images=2, progress=1.0)

    task.set_progress(0, 0)

    assert task.total_images == 0
    assert task.downloaded_images == 0
    assert task.progress == 0.0


def test_status_transition_rejects_impossible_jump() -> None:
    with pytest.raises(ValueError):
        validate_status_transition(TaskStatus.PAUSED, TaskStatus.COMPLETED)
    validate_status_transition(TaskStatus.PAUSED, TaskStatus.PENDING)
    assert TaskStatus.COMPLETED not in CANCELLABLE_TASK_STATUSES
    with pytest.raises(ValueError):
        validate_status_transition(TaskStatus.COMPLETED, TaskStatus.CANCELED)
