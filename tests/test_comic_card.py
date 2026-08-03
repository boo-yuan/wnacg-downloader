"""Search-card state regressions for queue and local artifact changes."""

from pathlib import Path
from typing import cast

import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from wnacg.application.file_paths import archive_path
from wnacg.application.ports import TaskRepository
from wnacg.domain.models import Comic, DownloadTask, TaskStatus
from wnacg.ui.components.comic_card import ComicCard, ComicCardState, resolve_comic_card_state
from wnacg.ui.components.cover_manager import CoverManagerClass


def _comic() -> Comic:
    return Comic(aid="123", title="State test", pic_count="2P")


def _task(path: Path, status: TaskStatus, *, downloaded: int = 0) -> DownloadTask:
    return DownloadTask(
        id="task-123",
        comic=_comic(),
        status=status,
        progress=downloaded / 2,
        total_images=2,
        downloaded_images=downloaded,
        save_path=str(path),
        download_root=str(path.parent),
    )


@pytest.mark.parametrize("status", [TaskStatus.PENDING, TaskStatus.DOWNLOADING, TaskStatus.PAUSED])
def test_active_task_is_shown_as_queued(tmp_path: Path, status: TaskStatus) -> None:
    path = tmp_path / "gallery"

    assert resolve_comic_card_state(_comic(), _task(path, status), path) is ComicCardState.QUEUED


def test_completed_task_requires_all_directory_images(tmp_path: Path) -> None:
    path = tmp_path / "gallery"
    path.mkdir()
    (path / "001.jpg").write_bytes(b"one")
    task = _task(path, TaskStatus.COMPLETED, downloaded=2)

    assert resolve_comic_card_state(_comic(), task, path) is ComicCardState.MISSING

    (path / "002.png").write_bytes(b"two")
    assert resolve_comic_card_state(_comic(), task, path) is ComicCardState.DOWNLOADED


def test_completed_archive_is_shown_as_downloaded(tmp_path: Path) -> None:
    path = tmp_path / "gallery"
    archive_path(path).write_bytes(b"zip payload")

    assert resolve_comic_card_state(_comic(), _task(path, TaskStatus.COMPLETED, downloaded=2), path) is (
        ComicCardState.DOWNLOADED
    )


def test_deleted_completed_artifacts_are_shown_as_missing(tmp_path: Path) -> None:
    path = tmp_path / "deleted-gallery"

    assert resolve_comic_card_state(_comic(), _task(path, TaskStatus.COMPLETED, downloaded=2), path) is (
        ComicCardState.MISSING
    )


def test_failed_task_and_untracked_download_have_distinct_states(tmp_path: Path) -> None:
    path = tmp_path / "gallery"
    failed = _task(path, TaskStatus.FAILED)
    assert resolve_comic_card_state(_comic(), failed, path) is ComicCardState.FAILED

    path.mkdir()
    (path / "001.jpg").write_bytes(b"one")
    (path / "002.jpg").write_bytes(b"two")
    assert resolve_comic_card_state(_comic(), None, path) is ComicCardState.DOWNLOADED


class _EmptyRepository:
    def get_task_by_aid(self, _aid: str) -> DownloadTask | None:
        return None


def test_card_buttons_expose_each_download_state() -> None:
    application = QApplication.instance()
    if application is None:
        application = QApplication([])
    assert isinstance(application, QApplication)

    pool = QThreadPool()
    card = ComicCard(
        _comic(),
        cast(TaskRepository, _EmptyRepository()),
        cast(CoverManagerClass, object()),
        pool,
    )
    generation = card._state_generation

    card._apply_download_state(generation, ComicCardState.QUEUED.value)
    assert card.downloadBtn.text() == "已添加到队列"
    assert not card.downloadBtn.isEnabled()

    card._apply_download_state(generation, ComicCardState.DOWNLOADED.value)
    assert card.openBtn.text() == "已下载 · 打开文件"
    assert not card.openBtn.isHidden()
    assert not card.can_queue_download

    card._apply_download_state(generation, ComicCardState.MISSING.value)
    assert card.downloadBtn.text() == "文件已删除 · 重新下载"
    assert card.can_queue_download

    pool.waitForDone()
    card.close()
    card.deleteLater()
    application.processEvents()
