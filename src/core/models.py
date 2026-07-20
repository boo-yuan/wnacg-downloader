from pydantic import BaseModel
from typing import Optional
from enum import Enum

class TaskStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"

class Comic(BaseModel):
    aid: str
    title: str
    cover_url: str
    url: str
    pic_count: str = ""
    date: str = ""

class DownloadTask(BaseModel):
    id: str
    comic: Comic
    status: TaskStatus = TaskStatus.PENDING
    progress: float = 0.0
    total_images: int = 0
    downloaded_images: int = 0
    save_path: str = ""
    error_message: Optional[str] = None
