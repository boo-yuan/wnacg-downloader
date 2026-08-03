"""Stress regressions for page-scoped search-card state snapshots."""

import time
from pathlib import Path
from typing import cast

from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt, Slot
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from wnacg.application.downloader import DownloaderWorker
from wnacg.application.ports import TaskRepository
from wnacg.domain.models import Comic, DownloadTask, TaskStatus
from wnacg.ui.card_state_coordinator import CardStateCoordinator
from wnacg.ui.components.comic_card import ComicCardState
from wnacg.ui.components.cover_manager import CoverManagerClass
from wnacg.ui.views.home_interface import HomeInterface


class _StateRepository:
    def __init__(self, tasks: list[DownloadTask], delay: float = 0.0) -> None:
        self._tasks = {task.comic.aid: task for task in tasks}
        self._delay = delay

    def get_task_by_aid(self, aid: str) -> DownloadTask | None:
        if self._delay:
            time.sleep(self._delay)
        return self._tasks.get(aid)


class _StateReceiver(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[tuple[int, dict[str, str]]] = []

    @Slot(int, object)
    def receive(self, generation: int, raw_states: object) -> None:
        if isinstance(raw_states, dict):
            state_items = cast(dict[object, object], raw_states).items()
            states = {aid: state for aid, state in state_items if isinstance(aid, str) and isinstance(state, str)}
            self.results.append((generation, states))


def _application() -> QApplication:
    application = QApplication.instance()
    if application is None:
        application = QApplication([])
    assert isinstance(application, QApplication)
    return application


def test_batch_snapshot_reports_current_card_states(tmp_path: Path) -> None:
    application = _application()
    completed_path = tmp_path / "completed"
    completed_path.mkdir()
    (completed_path / "001.jpg").write_bytes(b"one")
    comic = Comic(aid="completed", title="Completed", pic_count="1P")
    task = DownloadTask(
        id="completed",
        comic=comic,
        status=TaskStatus.COMPLETED,
        progress=1.0,
        total_images=1,
        downloaded_images=1,
        save_path=str(completed_path),
        download_root=str(tmp_path),
    )
    coordinator = CardStateCoordinator(cast(TaskRepository, _StateRepository([task])))
    receiver = _StateReceiver()
    coordinator.signals.finished.connect(receiver.receive, Qt.ConnectionType.QueuedConnection)

    generation = coordinator.request([comic])
    assert coordinator.wait_for_done(2_000)
    application.processEvents()

    assert receiver.results == [(generation, {comic.aid: ComicCardState.DOWNLOADED.value})]
    coordinator.stop()


def test_invalidated_snapshot_does_not_reach_receiver(tmp_path: Path) -> None:
    application = _application()
    comics = [Comic(aid=str(index), title=f"Comic {index}") for index in range(40)]
    repository = cast(TaskRepository, _StateRepository([], delay=0.002))
    coordinator = CardStateCoordinator(repository)
    receiver = _StateReceiver()
    coordinator.signals.finished.connect(receiver.receive, Qt.ConnectionType.QueuedConnection)

    coordinator.request(comics)
    coordinator.invalidate()
    assert coordinator.wait_for_done(2_000)
    application.processEvents()

    assert receiver.results == []
    coordinator.stop()


def test_destroyed_receivers_are_disconnected_during_state_stress() -> None:
    application = _application()
    comics = [Comic(aid=str(index), title=f"Comic {index}") for index in range(20)]
    coordinator = CardStateCoordinator(cast(TaskRepository, _StateRepository([], delay=0.001)))

    for _index in range(30):
        receiver = _StateReceiver()
        coordinator.signals.finished.connect(receiver.receive, Qt.ConnectionType.QueuedConnection)
        coordinator.request(comics)
        receiver.deleteLater()
        QCoreApplication.sendPostedEvents(receiver, QEvent.Type.DeferredDelete)
        assert not isValid(receiver)
        coordinator.invalidate()

    assert coordinator.wait_for_done(3_000)
    application.processEvents()
    coordinator.stop()


def test_home_page_can_rebuild_cards_while_state_snapshot_is_running() -> None:
    application = _application()
    comics = [Comic(aid=str(index), title=f"Comic {index}") for index in range(20)]
    repository = cast(TaskRepository, _StateRepository([], delay=0.001))
    home = HomeInterface(
        DownloaderWorker(repository),
        repository,
        cast(CoverManagerClass, object()),
    )
    home.current_keyword = "stress"
    home.show()

    for _index in range(20):
        home._on_search_result("stress", comics, 1, 1)
        home.hide()
        home._clear_cards()
        application.processEvents()
        home.show()

    home.stop_workers(time.monotonic() + 3.0)
    home.close()
    home.deleteLater()
    application.processEvents()
