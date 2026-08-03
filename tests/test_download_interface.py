"""Regressions for bounded download-queue widget ownership."""

from typing import cast

from PySide6.QtWidgets import QApplication

from wnacg.application.downloader import DownloaderWorker
from wnacg.application.ports import TaskRepository
from wnacg.domain.models import Comic, DownloadTask, TaskStatus
from wnacg.ui.views.download_interface import DownloadInterface


class PageRepository:
    def __init__(self, tasks: list[DownloadTask]) -> None:
        self.tasks = tasks
        self.page_calls: list[tuple[int, int]] = []

    def get_all_tasks(self) -> list[DownloadTask]:
        return list(self.tasks)

    def get_tasks_page(self, offset: int, limit: int) -> list[DownloadTask]:
        self.page_calls.append((offset, limit))
        return self.tasks[offset : offset + limit]

    def count_tasks(self, statuses: frozenset[TaskStatus] | None = None) -> int:
        if statuses is None:
            return len(self.tasks)
        return sum(task.status in statuses for task in self.tasks)


def test_download_interface_keeps_only_one_page_of_cards_alive() -> None:
    application = QApplication.instance()
    if application is None:
        application = QApplication([])
    assert isinstance(application, QApplication)

    tasks = [
        DownloadTask(id=f"task-{index}", comic=Comic(aid=str(index + 1), title=f"Comic {index}"))
        for index in range(250)
    ]
    repository = PageRepository(tasks)
    repository_port = cast(TaskRepository, repository)
    downloader = DownloaderWorker(repository_port)
    interface = DownloadInterface(downloader, repository_port)

    assert len(interface.task_cards) == 100
    assert repository.page_calls == [(0, 100)]
    assert "共 250 项" in interface.pageLabel.text()

    interface.nextPageButton.click()
    assert len(interface.task_cards) == 100
    assert repository.page_calls[-1] == (100, 100)

    interface.nextPageButton.click()
    assert len(interface.task_cards) == 50
    assert repository.page_calls[-1] == (200, 100)

    interface.close()
    interface.deleteLater()
    application.processEvents()
