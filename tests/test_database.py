from pathlib import Path

import pytest

from wnacg.domain.models import Comic, DownloadOptions, DownloadTask, TaskStatus
from wnacg.infrastructure import database


def test_task_and_image_round_trip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "tasks.db")
    database.initialize_database()
    task = DownloadTask(
        id="task-1",
        comic=Comic(aid="100", title="Title"),
        save_path=str(tmp_path / "downloads" / "Title [100]"),
        download_root=str(tmp_path / "downloads"),
        options=DownloadOptions(),
    )
    database.save_task(task)
    database.save_raw_links(task.id, ["https://img/one.jpg", "https://img/two.jpg"])

    loaded = database.get_task(task.id)
    assert loaded is not None
    assert loaded.options == task.options
    assert loaded.download_root == task.download_root
    assert [image["image_index"] for image in database.get_images(task.id)] == [0, 1]
    assert database.count_tasks() == 1
    assert database.count_tasks(frozenset({TaskStatus.PENDING})) == 1
    assert [page_task.id for page_task in database.get_tasks_page(0, 10)] == [task.id]

    database.update_task_status(task.id, TaskStatus.DOWNLOADING)
    assert database.get_task(task.id).status is TaskStatus.DOWNLOADING  # type: ignore[union-attr]


def test_pending_task_can_only_be_claimed_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "tasks.db")
    database.initialize_database()
    task = DownloadTask(id="task-1", comic=Comic(aid="100", title="Title"))
    database.save_task(task)

    assert database.claim_pending_task(task.id) is True
    assert database.claim_pending_task(task.id) is False
    claimed = database.get_task(task.id)
    assert claimed is not None
    assert claimed.status is TaskStatus.DOWNLOADING


def test_future_database_schema_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    database_path = tmp_path / "tasks.db"
    monkeypatch.setattr(database, "DATABASE_PATH", database_path)
    with database.sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA user_version = 999")

    with pytest.raises(RuntimeError, match="newer than supported"):
        database.initialize_database()


def test_database_rejects_invalid_progress(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "tasks.db")
    database.initialize_database()
    task = DownloadTask(id="task-1", comic=Comic(aid="100", title="Title"))
    database.save_task(task)

    with pytest.raises(ValueError, match="Invalid task progress"):
        database.update_task_progress(task.id, 1.0, 2, 1)


def test_image_updates_progress_reset_and_delete(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "tasks.db")
    database.initialize_database()
    task = DownloadTask(id="task-1", comic=Comic(aid="100", title="Title"))
    database.save_task(task)
    database.save_view_links(task.id, ["https://www.wnacg.com/view-1", "https://www.wnacg.com/view-2"])
    database.update_image_raw_url(task.id, 0, "https://img.example/one.jpg")
    database.update_image_status(task.id, 0, "downloaded")
    images = database.get_images(task.id)
    assert images[0]["raw_url"] == "https://img.example/one.jpg"
    assert images[0]["status"] == "downloaded"

    database.update_task_progress(task.id, 0.5, 1, 2)
    database.update_task_status(task.id, TaskStatus.DOWNLOADING)
    database.reset_downloading_tasks()
    loaded = database.get_task_by_aid("100")
    assert loaded is not None
    assert loaded.status is TaskStatus.PAUSED
    assert loaded.downloaded_images == 1

    database.delete_task(task.id)
    assert database.get_task(task.id) is None
    assert database.get_images(task.id) == []


def test_save_task_enforces_status_transitions_and_unique_gallery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(database, "DATABASE_PATH", tmp_path / "tasks.db")
    database.initialize_database()
    first = DownloadTask(id="first", comic=Comic(aid="100", title="Title"))
    database.save_task(first)

    first.status = TaskStatus.COMPLETED
    with pytest.raises(ValueError, match="Invalid task status transition"):
        database.save_task(first)

    duplicate = DownloadTask(id="duplicate", comic=Comic(aid="100", title="Duplicate"))
    with pytest.raises(ValueError, match="already tracked"):
        database.save_task(duplicate)
