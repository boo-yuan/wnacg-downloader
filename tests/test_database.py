from pathlib import Path

from wnacg.domain.models import Comic, DownloadOptions, DownloadTask, TaskStatus
from wnacg.infrastructure import database


def test_task_and_image_round_trip(monkeypatch, tmp_path: Path) -> None:
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

    database.update_task_status(task.id, TaskStatus.DOWNLOADING)
    assert database.get_task(task.id).status is TaskStatus.DOWNLOADING  # type: ignore[union-attr]
