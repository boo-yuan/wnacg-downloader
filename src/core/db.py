import sqlite3
from contextlib import closing
from pathlib import Path
import json
from typing import List, Optional
from core.models import DownloadTask, Comic, TaskStatus

DB_PATH = Path("tasks.db")

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with closing(get_conn()) as conn, conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                aid TEXT,
                title TEXT,
                cover_url TEXT,
                url TEXT,
                status TEXT,
                progress REAL,
                total_images INTEGER,
                downloaded_images INTEGER,
                save_path TEXT,
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Create an images table for mapping image index to its URL
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS images (
                task_id TEXT,
                image_index INTEGER,
                view_url TEXT,
                raw_url TEXT,
                status TEXT DEFAULT 'pending',
                PRIMARY KEY (task_id, image_index),
                FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
            )
        """)
        conn.commit()

def save_task(task: DownloadTask):
    with closing(get_conn()) as conn, conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO tasks 
            (id, aid, title, cover_url, url, status, progress, total_images, downloaded_images, save_path, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            task.id, task.comic.aid, task.comic.title, task.comic.cover_url, task.comic.url,
            task.status.value, task.progress, task.total_images, task.downloaded_images,
            task.save_path, task.error_message
        ))
        conn.commit()

def get_all_tasks() -> List[DownloadTask]:
    with closing(get_conn()) as conn, conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks ORDER BY created_at DESC")
        rows = cursor.fetchall()
        
        tasks = []
        for row in rows:
            comic = Comic(
                aid=row["aid"],
                title=row["title"],
                cover_url=row["cover_url"],
                url=row["url"]
            )
            task = DownloadTask(
                id=row["id"],
                comic=comic,
                status=TaskStatus(row["status"]),
                progress=row["progress"],
                total_images=row["total_images"],
                downloaded_images=row["downloaded_images"],
                save_path=row["save_path"],
                error_message=row["error_message"]
            )
            tasks.append(task)
        return tasks

def get_task(task_id: str) -> Optional[DownloadTask]:
    with closing(get_conn()) as conn, conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        if not row:
            return None
            
        comic = Comic(
            aid=row["aid"],
            title=row["title"],
            cover_url=row["cover_url"],
            url=row["url"]
        )
        return DownloadTask(
            id=row["id"],
            comic=comic,
            status=TaskStatus(row["status"]),
            progress=row["progress"],
            total_images=row["total_images"],
            downloaded_images=row["downloaded_images"],
            save_path=row["save_path"],
            error_message=row["error_message"]
        )

def get_task_by_aid(aid: str) -> Optional[DownloadTask]:
    with closing(get_conn()) as conn, conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE aid = ?", (aid,))
        row = cursor.fetchone()
        if not row:
            return None
            
        comic = Comic(
            aid=row["aid"],
            title=row["title"],
            cover_url=row["cover_url"],
            url=row["url"]
        )
        return DownloadTask(
            id=row["id"],
            comic=comic,
            status=TaskStatus(row["status"]),
            progress=row["progress"],
            total_images=row["total_images"],
            downloaded_images=row["downloaded_images"],
            save_path=row["save_path"],
            error_message=row["error_message"]
        )

def update_task_status(task_id: str, status: TaskStatus, error_message: str = None):
    with closing(get_conn()) as conn, conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE tasks SET status = ?, error_message = ? WHERE id = ?", (status.value, error_message, task_id))
        conn.commit()

def update_task_progress(task_id: str, progress: float, downloaded: int, total: int):
    with closing(get_conn()) as conn, conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE tasks SET progress = ?, downloaded_images = ?, total_images = ? WHERE id = ?", 
                       (progress, downloaded, total, task_id))
        conn.commit()

def delete_task(task_id: str):
    with closing(get_conn()) as conn, conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        cursor.execute("DELETE FROM images WHERE task_id = ?", (task_id,))
        conn.commit()

def reset_downloading_tasks():
    with closing(get_conn()) as conn, conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE tasks SET status = ? WHERE status = ?", (TaskStatus.PAUSED.value, TaskStatus.DOWNLOADING.value))
        conn.commit()

def save_view_links(task_id: str, view_links: List[str]):
    with closing(get_conn()) as conn, conn:
        cursor = conn.cursor()
        for i, link in enumerate(view_links):
            cursor.execute("""
                INSERT OR IGNORE INTO images (task_id, image_index, view_url) 
                VALUES (?, ?, ?)
            """, (task_id, i, link))
        conn.commit()

def get_images(task_id: str):
    with closing(get_conn()) as conn, conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM images WHERE task_id = ? ORDER BY image_index ASC", (task_id,))
        return [dict(row) for row in cursor.fetchall()]

def update_image_raw_url(task_id: str, image_index: int, raw_url: str):
    with closing(get_conn()) as conn, conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE images SET raw_url = ? WHERE task_id = ? AND image_index = ?", (raw_url, task_id, image_index))
        conn.commit()

def update_image_status(task_id: str, image_index: int, status: str):
    with closing(get_conn()) as conn, conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE images SET status = ? WHERE task_id = ? AND image_index = ?", (status, task_id, image_index))
        conn.commit()

# Ensure DB is initialized
init_db()
