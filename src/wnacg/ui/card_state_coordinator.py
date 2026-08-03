"""Page-scoped, cancellation-aware search-card state snapshots."""

import threading
import time
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from wnacg.application.file_paths import task_directory
from wnacg.application.ports import TaskRepository
from wnacg.domain.models import Comic
from wnacg.infrastructure.config import cfg
from wnacg.infrastructure.logger import logger
from wnacg.ui.components.comic_card import resolve_comic_card_state


class CardStateSignals(QObject):
    """Long-lived relay whose receiver is tracked by Qt."""

    finished = Signal(int, object)  # generation, dict[aid, state]


class _CardStateBatchWorker(QRunnable):
    def __init__(
        self,
        generation: int,
        comics: tuple[Comic, ...],
        fallback_root: Path,
        repository: TaskRepository,
        signals: CardStateSignals,
        canceled: threading.Event,
    ) -> None:
        super().__init__()
        self._generation = generation
        self._comics = comics
        self._fallback_root = fallback_root
        self._repository = repository
        self._signals = signals
        self._canceled = canceled

    @Slot()
    def run(self) -> None:
        states: dict[str, str] = {}
        for comic in self._comics:
            if self._canceled.is_set():
                return
            try:
                task = self._repository.get_task_by_aid(comic.aid)
                fallback_path = task_directory(self._fallback_root, comic.title)
                states[comic.aid] = resolve_comic_card_state(comic, task, fallback_path).value
            except Exception as error:
                logger.warning("Search-card state lookup failed", aid=comic.aid, error=str(error))
        if not self._canceled.is_set():
            self._signals.finished.emit(self._generation, states)


class CardStateCoordinator(QObject):
    """Own one bounded state worker and invalidate snapshots before card destruction."""

    def __init__(self, repository: TaskRepository, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.signals = CardStateSignals(self)
        self._repository = repository
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._generation = 0
        self._active_cancel: threading.Event | None = None
        self._stopped = False

    @property
    def generation(self) -> int:
        return self._generation

    def request(self, comics: Sequence[Comic]) -> int:
        """Replace queued work with one immutable snapshot request."""
        if self._stopped:
            return self._generation
        self._cancel_active()
        self._generation += 1
        generation = self._generation
        comic_snapshot = tuple(comics)
        if not comic_snapshot:
            self.signals.finished.emit(generation, {})
            return generation

        canceled = threading.Event()
        self._active_cancel = canceled
        worker = _CardStateBatchWorker(
            generation,
            comic_snapshot,
            Path(cfg.download_dir),
            self._repository,
            self.signals,
            canceled,
        )
        self._pool.start(worker)
        return generation

    def invalidate(self) -> int:
        """Make every in-flight result stale before its cards are removed."""
        self._cancel_active()
        self._generation += 1
        return self._generation

    def _cancel_active(self) -> None:
        if self._active_cancel is not None:
            self._active_cancel.set()
            self._active_cancel = None
        self._pool.clear()

    def wait_for_done(self, timeout_milliseconds: int) -> bool:
        """Wait for tests or orderly shutdown without exposing the worker pool."""
        return self._pool.waitForDone(max(0, timeout_milliseconds))

    def stop(self, deadline: float | None = None) -> None:
        """Cancel queued snapshots and join a currently running repository read."""
        if self._stopped:
            return
        self._stopped = True
        self.invalidate()
        shutdown_deadline = deadline or (time.monotonic() + 8.0)
        remaining_milliseconds = max(0, int((shutdown_deadline - time.monotonic()) * 1_000))
        if not self._pool.waitForDone(remaining_milliseconds):
            logger.warning("Search-card state worker did not stop within deadline")
            self._pool.waitForDone()
