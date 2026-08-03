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
_SCHEMA_VERSION = 5
_TASK_QUEUE_QUERY = """
    SELECT *
    FROM tasks
    ORDER BY
        CASE
            WHEN status = 'downloading' THEN 0
            WHEN status IN ('pending', 'paused', 'failed', 'missing') THEN 1
            WHEN status = 'completed' THEN 2
            ELSE 3
        END,
        created_at ASC,
        rowid ASC
    LIMIT ? OFFSET ?
"""


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
    with closing(_connect()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()


def _column_names(connection: sqlite3.Connection, table_name: str) -> set[str]:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(row["name"]) for row in rows}


def initialize_database() -> None:
    """Create the database and migrate databases produced by legacy releases."""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect()) as journal_connection:
        journal_connection.execute("PRAGMA journal_mode = WAL")
    with _transaction() as connection:
        current_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if current_version > _SCHEMA_VERSION:
            raise RuntimeError(
                f"Database schema version {current_version} is newer than supported version {_SCHEMA_VERSION}"
            )
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
                status TEXT NOT NULL CHECK (
                    status IN ('pending', 'downloading', 'paused', 'completed', 'failed', 'missing', 'canceled')
                ),
                progress REAL NOT NULL CHECK (progress >= 0.0 AND progress <= 1.0),
                total_images INTEGER NOT NULL CHECK (total_images >= 0),
                downloaded_images INTEGER NOT NULL CHECK (downloaded_images >= 0 AND downloaded_images <= total_images),
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
                status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'downloaded')),
                output_name TEXT NOT NULL DEFAULT '',
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
        legacy_image_columns = _column_names(connection, "images")
        if "output_name" not in legacy_image_columns:
            connection.execute("ALTER TABLE images ADD COLUMN output_name TEXT NOT NULL DEFAULT ''")

        connection.execute("CREATE INDEX IF NOT EXISTS idx_tasks_aid ON tasks(aid)")
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS validate_task_progress_insert
            BEFORE INSERT ON tasks
            WHEN NEW.progress < 0.0 OR NEW.progress > 1.0
              OR NEW.total_images < 0 OR NEW.downloaded_images < 0
              OR NEW.downloaded_images > NEW.total_images
              OR NEW.status NOT IN ('pending', 'downloading', 'paused', 'completed', 'failed', 'missing', 'canceled')
            BEGIN
                SELECT RAISE(ABORT, 'invalid task aggregate');
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS validate_task_progress_update
            BEFORE UPDATE ON tasks
            WHEN NEW.progress < 0.0 OR NEW.progress > 1.0
              OR NEW.total_images < 0 OR NEW.downloaded_images < 0
              OR NEW.downloaded_images > NEW.total_images
              OR NEW.status NOT IN ('pending', 'downloading', 'paused', 'completed', 'failed', 'missing', 'canceled')
            BEGIN
                SELECT RAISE(ABORT, 'invalid task aggregate');
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS validate_image_status_insert
            BEFORE INSERT ON images
            WHEN NEW.status NOT IN ('pending', 'downloaded')
            BEGIN
                SELECT RAISE(ABORT, 'invalid image status');
            END
            """
        )
        connection.execute(
            """
            CREATE TRIGGER IF NOT EXISTS validate_image_status_update
            BEFORE UPDATE ON images
            WHEN NEW.status NOT IN ('pending', 'downloaded')
            BEGIN
                SELECT RAISE(ABORT, 'invalid image status');
            END
            """
        )
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
        existing = connection.execute("SELECT status FROM tasks WHERE id = ?", (task.id,)).fetchone()
        if existing is not None:
            validate_status_transition(TaskStatus(str(existing["status"])), task.status)
        duplicate = connection.execute(
            "SELECT id FROM tasks WHERE aid = ? AND id <> ? LIMIT 1",
            (task.comic.aid, task.id),
        ).fetchone()
        if duplicate is not None:
            raise ValueError(f"Gallery {task.comic.aid} is already tracked by task {duplicate['id']}")
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
    """Return tasks in the same priority and FIFO order used by the queue UI."""
    with closing(_connect()) as connection:
        rows = connection.execute(_TASK_QUEUE_QUERY, (-1, 0)).fetchall()
    tasks: list[DownloadTask] = []
    for row in rows:
        try:
            tasks.append(_task_from_row(row))
        except (ValueError, ValidationError) as error:
            logger.error("Quarantining invalid persisted task from listings", task_id=str(row["id"]), error=str(error))
    return tasks


def get_tasks_page(offset: int, limit: int) -> list[DownloadTask]:
    """Return one bounded page of tasks in the same order as the full listing."""
    if offset < 0 or limit < 1:
        raise ValueError("Task page offset and limit must be positive")
    with closing(_connect()) as connection:
        rows = connection.execute(_TASK_QUEUE_QUERY, (limit, offset)).fetchall()
    tasks: list[DownloadTask] = []
    for row in rows:
        try:
            tasks.append(_task_from_row(row))
        except (ValueError, ValidationError) as error:
            logger.error("Quarantining invalid persisted task from page", task_id=str(row["id"]), error=str(error))
    return tasks


def count_tasks(statuses: frozenset[TaskStatus] | None = None) -> int:
    """Count tasks without materializing every persisted aggregate."""
    with closing(_connect()) as connection:
        if statuses is None:
            row = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()
            return int(row[0]) if row is not None else 0
        if not statuses:
            return 0
        rows = connection.execute("SELECT status, COUNT(*) AS count FROM tasks GROUP BY status").fetchall()
        counts = {TaskStatus(str(status_row["status"])): int(status_row["count"]) for status_row in rows}
        return sum(counts.get(status, 0) for status in statuses)


def get_task(task_id: str) -> DownloadTask | None:
    """Return a task by identifier."""
    with closing(_connect()) as connection:
        row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        return None
    try:
        return _task_from_row(row)
    except (ValueError, ValidationError) as error:
        logger.error("Invalid persisted task cannot be loaded", task_id=task_id, error=str(error))
        return None


def get_task_by_aid(aid: str) -> DownloadTask | None:
    """Return the newest task associated with a gallery identifier."""
    with closing(_connect()) as connection:
        row = connection.execute(
            "SELECT * FROM tasks WHERE aid = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            (aid,),
        ).fetchone()
    if row is None:
        return None
    try:
        return _task_from_row(row)
    except (ValueError, ValidationError) as error:
        logger.error("Invalid persisted task cannot be loaded", task_id=str(row["id"]), error=str(error))
        return None


def update_task_status(
    task_id: str,
    status: TaskStatus,
    error_message: str | None = None,
) -> bool:
    """Persist a task status and its optional failure message."""
    with _transaction() as connection:
        row = connection.execute("SELECT status FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return False
        validate_status_transition(TaskStatus(str(row["status"])), status)
        cursor = connection.execute(
            "UPDATE tasks SET status = ?, error_message = ? WHERE id = ?",
            (status.value, error_message, task_id),
        )
        return cursor.rowcount == 1


def claim_pending_task(task_id: str) -> bool:
    """Atomically claim exactly one pending task for a downloader process."""
    with _transaction() as connection:
        cursor = connection.execute(
            """
            UPDATE tasks
            SET status = ?, error_message = NULL
            WHERE id = ? AND status = ?
            """,
            (TaskStatus.DOWNLOADING.value, task_id, TaskStatus.PENDING.value),
        )
        return cursor.rowcount == 1


def update_task_progress(task_id: str, progress: float, downloaded: int, total: int) -> None:
    """Persist aggregate image progress for a task."""
    if total < 0 or downloaded < 0 or downloaded > total or not 0.0 <= progress <= 1.0:
        raise ValueError("Invalid task progress aggregate")
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
            output_name=str(row["output_name"] or ""),
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


def update_image_status(task_id: str, image_index: int, status: str, output_name: str = "") -> None:
    """Atomically persist image status and its validated final direct-child name."""
    if status not in {"pending", "downloaded"}:
        raise ValueError(f"Invalid image status: {status}")
    if status == "pending":
        output_name = ""
    candidate = Path(output_name)
    if status == "downloaded" and (
        not output_name or len(output_name) > 255 or candidate.name != output_name or output_name in {".", ".."}
    ):
        raise ValueError(f"Invalid image output name: {output_name}")
    with _transaction() as connection:
        connection.execute(
            "UPDATE images SET status = ?, output_name = ? WHERE task_id = ? AND image_index = ?",
            (status, output_name, task_id, image_index),
        )


class SQLiteTaskRepository:
    """Concrete SQLite adapter implementing the application persistence port."""

    save_task = staticmethod(save_task)
    get_all_tasks = staticmethod(get_all_tasks)
    get_tasks_page = staticmethod(get_tasks_page)
    count_tasks = staticmethod(count_tasks)
    get_task = staticmethod(get_task)
    get_task_by_aid = staticmethod(get_task_by_aid)
    update_task_status = staticmethod(update_task_status)
    claim_pending_task = staticmethod(claim_pending_task)
    update_task_progress = staticmethod(update_task_progress)
    delete_task = staticmethod(delete_task)
    reset_downloading_tasks = staticmethod(reset_downloading_tasks)
    save_view_links = staticmethod(save_view_links)
    save_raw_links = staticmethod(save_raw_links)
    get_images = staticmethod(get_images)
    update_image_raw_url = staticmethod(update_image_raw_url)
    update_image_status = staticmethod(update_image_status)
