"""Application-entry regressions for locking and complete UI construction."""

import sys
import uuid

import pytest
from PySide6.QtCore import QLockFile

import wnacg.main as entrypoint
from wnacg.domain.models import Comic, DownloadTask, TaskStatus
from wnacg.infrastructure import database
from wnacg.infrastructure.paths import DATA_DIR
from wnacg.ui.main_window import calculate_window_sizes


def test_window_sizes_are_dpi_independent_and_screen_safe() -> None:
    assert calculate_window_sizes(2560, 1400) == ((1200, 720), (960, 600))
    assert calculate_window_sizes(1707, 900) == ((1200, 720), (960, 600))
    assert calculate_window_sizes(1280, 680) == ((1184, 584), (960, 584))


def test_second_instance_does_not_reset_live_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    database.initialize_database()
    identifier = uuid.uuid4().hex
    task = DownloadTask(id=identifier, comic=Comic(aid=identifier, title="Live task"))
    database.save_task(task)
    database.update_task_status(task.id, TaskStatus.DOWNLOADING)

    lock = QLockFile(str(DATA_DIR / "application.lock"))
    lock.setStaleLockTime(0)
    assert lock.tryLock(100)
    try:
        monkeypatch.setattr(sys, "argv", ["wnacg-downloader"])
        assert entrypoint.main() == 2
    finally:
        lock.unlock()

    persisted = database.get_task(task.id)
    assert persisted is not None
    assert persisted.status is TaskStatus.DOWNLOADING


def test_application_smoke_constructs_and_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(sys, "argv", ["wnacg-downloader", "--smoke-test"])

    assert entrypoint.main() == 0
