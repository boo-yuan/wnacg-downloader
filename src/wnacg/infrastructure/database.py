"""SQLite persistence adapter for download tasks and image progress."""

import sqlite3
from collections.abc import Generator
from contextlib import closing, contextmanager
from pathlib import Path

from pydantic import ValidationError

from wnacg.application.ports import ImageRecord
from wnacg.domain.models import (
    Comic,
    DownloadOptions,
    DownloadTask,
    TaskStatus,
    validate_status_transition,
)
from wnacg.infrastructure.logger import logger
from wnacg.infrastructure.paths import DATA_DIR

DATABASE_PATH = DATA_DIR / "tasks.db"
_BUSY_TIMEOUT_MILLISECONDS = 30_000
_SCHEMA_VERSION = 3


def _connect() -> sqlite3.Connection:
    """Create a short-lived connection configured for concurrent workers."""
    connection = sqlite3.connect(DATABASE_PATH, timeout=_BUSY_TIMEOUT_MILLISECONDS / 1_000)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MILLISECONDS}")
    return connection


@contextmanager
def _transaction() -> Generator[sqlite3.Connection]:
    """Yield a connection and commit or roll back the transaction atomically."""
    with closing(_connect()) as connection, connection:
        yield connection


def _column_names(connection: sqlite3.Connection, table_name: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row["name"]) for row in rows}


def initialize_database() -> None:
    """Create the database and migrate databases produced by legacy releases."""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _transaction() as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                aid TEXT NOT NULL,
                title TEXT NOT NULL,
                cover_url TEXT NOT NULL,
                url TEXT NOT NULL,
                pic_count TEXT NOT NULL DEFAULT '',
                date TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                progress REAL NOT NULL,
                total_images INTEGER NOT NULL,
                downloaded_images INTEGER NOT NULL,
                save_path TEXT NOT NULL,
                download_root TEXT NOT NULL DEFAULT '',
                options_json TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS images (
                task_id TEXT NOT NULL,
                image_index INTEGER NOT NULL,
                view_url TEXT NOT NULL DEFAULT '',
                raw_url TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                PRIMARY KEY (task_id, image_index),
                FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
            )
            """
        )

        legacy_task_columns = _column_names(connection, "tasks")
        if "pic_count" not in legacy_task_columns:
            connection.execute("ALTER TABLE tasks ADD COLUMN pic_count TEXT NOT NULL DEFAULT ''")
        if "date" not in legacy_task_columns:
            connection.execute("ALTER TABLE tasks ADD COLUMN date TEXT NOT NULL DEFAULT ''")
        if "download_root" not in legacy_task_columns:
            connection.execute("ALTER TABLE tasks ADD COLUMN download_root TEXT NOT NULL DEFAULT ''")
        if "options_json" not in legacy_task_columns:
            connection.execute("ALTER TABLE tasks ADD COLUMN options_json TEXT")

        connection.execute("CREATE INDEX IF NOT EXISTS idx_tasks_aid ON tasks(aid)")
        connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")


def _task_from_row(row: sqlite3.Row) -> DownloadTask:
    save_path = str(row["save_path"])
    download_root = str(row["download_root"] or "")
    if not download_root and save_path:
        download_root = str(Path(save_path).parent)
    options_json = row["options_json"]
    try:
        options = DownloadOptions.model_validate_json(options_json) if options_json else None
    except ValidationError as error:
        logger.warning("Ignoring invalid persisted download options", task_id=str(row["id"]), error=str(error))
        options = None
    comic = Comic(
        aid=str(row["aid"]),
        title=str(row["title"]),
        cover_url=str(row["cover_url"]),
        url=str(row["url"]),
        pic_count=str(row["pic_count"] or ""),
        date=str(row["date"] or ""),
    )
    return DownloadTask(
        id=str(row["id"]),
        comic=comic,
        status=TaskStatus(str(row["status"])),
        progress=float(row["progress"]),
        total_images=int(row["total_images"]),
        downloaded_images=int(row["downloaded_images"]),
        save_path=save_path,
        download_root=download_root,
        options=options,
        error_message=row["error_message"],
    )


def save_task(task: DownloadTask) -> None:
    """Insert or update a task without deleting its persisted image rows."""
    with _transaction() as connection:
        connection.execute(
            """
            INSERT INTO tasks (
                id, aid, title, cover_url, url, pic_count, date, status, progress,
                total_images, downloaded_images, save_path, download_root,
                options_json, error_message
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                aid = excluded.aid,
                title = excluded.title,
                cover_url = excluded.cover_url,
                url = excluded.url,
                pic_count = excluded.pic_count,
                date = excluded.date,
                status = excluded.status,
                progress = excluded.progress,
                total_images = excluded.total_images,
                downloaded_images = excluded.downloaded_images,
                save_path = excluded.save_path,
                download_root = excluded.download_root,
                options_json = excluded.options_json,
                error_message = excluded.error_message
            """,
            (
                task.id,
                task.comic.aid,
                task.comic.title,
                task.comic.cover_url,
                task.comic.url,
                task.comic.pic_count,
                task.comic.date,
                task.status.value,
                task.progress,
                task.total_images,
                task.downloaded_images,
                task.save_path,
                task.download_root,
                task.options.model_dump_json() if task.options is not None else None,
                task.error_message,
            ),
        )


def get_all_tasks() -> list[DownloadTask]:
    """Return all tasks, newest first."""
    with closing(_connect()) as connection:
        rows = connection.execute("SELECT * FROM tasks ORDER BY created_at DESC, id DESC").fetchall()
    return [_task_from_row(row) for row in rows]


def get_task(task_id: str) -> DownloadTask | None:
    """Return a task by identifier."""
    with closing(_connect()) as connection:
        row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return _task_from_row(row) if row is not None else None


def get_task_by_aid(aid: str) -> DownloadTask | None:
    """Return the newest task associated with a gallery identifier."""
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT * FROM tasks WHERE aid = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            (aid,),
        ).fetchone()
    return _task_from_row(row) if row is not None else None


def update_task_status(
    task_id: str,
    status: TaskStatus,
    error_message: str | None = None,
) -> None:
    """Persist a task status and its optional failure message."""
    with _transaction() as connection:
        row = connection.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return
        validate_status_transition(TaskStatus(str(row["status"])), status)
        connection.execute(
            "UPDATE tasks SET status = ?, error_message = ? WHERE id = ?",
            (status.value, error_message, task_id),
        )


def update_task_progress(task_id: str, progress: float, downloaded: int, total: int) -> None:
    """Persist aggregate image progress for a task."""
    with _transaction() as connection:
        connection.execute(
            """
            UPDATE tasks
            SET progress = ?, downloaded_images = ?, total_images = ?
            WHERE id = ?
            """,
            (progress, downloaded, total, task_id),
        )


def delete_task(task_id: str) -> None:
    """Delete a task and all of its image rows."""
    with _transaction() as connection:
        connection.execute("DELETE FROM images WHERE task_id = ?", (task_id,))
        connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,))


def reset_downloading_tasks() -> None:
    """Pause tasks interrupted by an earlier application shutdown."""
    with _transaction() as connection:
        connection.execute(
            "UPDATE tasks SET status = ? WHERE status = ?",
            (TaskStatus.PAUSED.value, TaskStatus.DOWNLOADING.value),
        )


def save_view_links(task_id: str, view_links: list[str]) -> None:
    """Persist gallery view-page links in their display order."""
    rows = [(task_id, index, link) for index, link in enumerate(view_links)]
    with _transaction() as connection:
        connection.executemany(
            """
            INSERT INTO images (task_id, image_index, view_url)
            VALUES (?, ?, ?)
            ON CONFLICT(task_id, image_index) DO UPDATE SET view_url = excluded.view_url
            """,
            rows,
        )


def save_raw_links(task_id: str, raw_urls: list[str]) -> None:
    """Persist direct image links in their display order."""
    rows = [(task_id, index, link) for index, link in enumerate(raw_urls)]
    with _transaction() as connection:
        connection.executemany(
            """
            INSERT INTO images (task_id, image_index, raw_url)
            VALUES (?, ?, ?)
            ON CONFLICT(task_id, image_index) DO UPDATE SET raw_url = excluded.raw_url
            """,
            rows,
        )


def get_images(task_id: str) -> list[ImageRecord]:
    """Return persisted image rows in download order."""
    with closing(_connect()) as connection:
        rows = connection.execute(
            "SELECT * FROM images WHERE task_id = ? ORDER BY image_index ASC",
            (task_id,),
        ).fetchall()
    return [
        ImageRecord(
            task_id=str(row["task_id"]),
            image_index=int(row["image_index"]),
            view_url=str(row["view_url"] or ""),
            raw_url=str(row["raw_url"] or ""),
            status=str(row["status"]),
        )
        for row in rows
    ]


def update_image_raw_url(task_id: str, image_index: int, raw_url: str) -> None:
    """Persist a resolved direct URL for one image."""
    with _transaction() as connection:
        connection.execute(
            "UPDATE images SET raw_url = ? WHERE task_id = ? AND image_index = ?",
            (raw_url, task_id, image_index),
        )


def update_image_status(task_id: str, image_index: int, status: str) -> None:
    """Persist the download status of one image."""
    with _transaction() as connection:
        connection.execute(
            "UPDATE images SET status = ? WHERE task_id = ? AND image_index = ?",
            (status, task_id, image_index),
        )


class SQLiteTaskRepository:
    """Concrete SQLite adapter implementing the application persistence port."""

    save_task = staticmethod(save_task)
    get_all_tasks = staticmethod(get_all_tasks)
    get_task = staticmethod(get_task)
    get_task_by_aid = staticmethod(get_task_by_aid)
    update_task_status = staticmethod(update_task_status)
    update_task_progress = staticmethod(update_task_progress)
    delete_task = staticmethod(delete_task)
    reset_downloading_tasks = staticmethod(reset_downloading_tasks)
    save_view_links = staticmethod(save_view_links)
    save_raw_links = staticmethod(save_raw_links)
    get_images = staticmethod(get_images)
    update_image_raw_url = staticmethod(update_image_raw_url)
    update_image_status = staticmethod(update_image_status)


task_repository = SQLiteTaskRepository()
