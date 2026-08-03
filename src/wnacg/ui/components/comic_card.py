"""Search-result card and bounded asynchronous task-state lookup."""
# pyright: reportUnknownMemberType=false

import re
from enum import StrEnum
from pathlib import Path

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QColor, QContextMenuEvent, QImage, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, ElevatedCardWidget, PrimaryPushButton, ThemeColor

from wnacg.application.file_paths import archive_path, task_directory
from wnacg.application.ports import TaskRepository
from wnacg.domain.models import Comic, DownloadTask, TaskStatus
from wnacg.infrastructure.config import cfg
from wnacg.ui.components.cover_manager import CoverManagerClass
from wnacg.ui.components.selectable_container import SelectableContainer
from wnacg.ui.open_path import open_local_path

_IMAGE_EXTENSIONS = {".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}


class ComicCardState(StrEnum):
    """User-visible availability state for one search-result card."""

    CHECKING = "checking"
    AVAILABLE = "available"
    QUEUED = "queued"
    DOWNLOADED = "downloaded"
    MISSING = "missing"
    FAILED = "failed"


def _expected_image_count(comic: Comic, task: DownloadTask | None) -> int:
    if task is not None and task.total_images > 0:
        return task.total_images
    expected_match = re.search(r"\d+", comic.pic_count)
    return int(expected_match.group()) if expected_match else 0


def _artifacts_are_complete(path: Path, expected_images: int) -> bool:
    archive = archive_path(path)
    try:
        if archive.is_file() and archive.stat().st_size > 0:
            return True
        if not path.is_dir():
            return False
        image_count = sum(file.is_file() and file.suffix.lower() in _IMAGE_EXTENSIONS for file in path.iterdir())
    except OSError:
        return False
    return image_count >= expected_images if expected_images > 0 else image_count > 0


def resolve_comic_card_state(comic: Comic, task: DownloadTask | None, fallback_path: Path) -> ComicCardState:
    """Resolve queue and artifact state without touching Qt widgets."""
    path = Path(task.save_path) if task is not None else fallback_path
    if task is not None and task.status in (TaskStatus.PENDING, TaskStatus.DOWNLOADING, TaskStatus.PAUSED):
        return ComicCardState.QUEUED
    if task is not None and task.status is TaskStatus.FAILED:
        return ComicCardState.FAILED

    artifacts_complete = _artifacts_are_complete(path, _expected_image_count(comic, task))
    if task is not None and task.status in (TaskStatus.COMPLETED, TaskStatus.MISSING):
        return ComicCardState.DOWNLOADED if artifacts_complete else ComicCardState.MISSING
    if artifacts_complete:
        return ComicCardState.DOWNLOADED
    return ComicCardState.AVAILABLE


class ComicCard(ElevatedCardWidget):
    downloadClicked = Signal(Comic)

    def __init__(
        self,
        comic: Comic,
        repository: TaskRepository,
        cover_manager: CoverManagerClass,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.comic = comic
        self._repository = repository
        self._cover_manager = cover_manager
        self.setFixedWidth(220)

        self.vbox = QVBoxLayout(self)
        self.vbox.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.vbox.setSpacing(6)
        self.vbox.setContentsMargins(12, 12, 12, 12)

        self._is_selected = False
        self._download_state = ComicCardState.CHECKING

        # 封面图
        self.coverLabel = QLabel(self)
        self.coverLabel.setFixedSize(196, 250)
        self.coverLabel.setScaledContents(True)
        self.coverLabel.setStyleSheet("background-color: rgba(0,0,0,0.05); border-radius: 8px;")

        # 标题 (完整显示，自适应高度)
        self.titleLabel = QLabel(comic.title, self)
        self.titleLabel.setWordWrap(True)
        self.titleLabel.setFixedWidth(196)
        # 强制设置固定高度，防止在布局自适应时与封面发生浮动重叠
        font_metrics = self.titleLabel.fontMetrics()
        rect = font_metrics.boundingRect(0, 0, 196, 9999, Qt.TextFlag.TextWordWrap, comic.title)
        title_h = rect.height() + 5
        self.titleLabel.setFixedHeight(title_h)

        # 信息行
        self.infoLayout = QHBoxLayout()
        self.infoLayout.setContentsMargins(0, 0, 0, 0)
        self.picCountLabel = CaptionLabel(comic.pic_count, self)
        self.picCountLabel.setFixedHeight(20)
        self.picCountLabel.setTextColor(ThemeColor.PRIMARY.color())
        self.dateLabel = CaptionLabel(comic.date, self)
        self.dateLabel.setFixedHeight(20)
        self.dateLabel.setTextColor(QColor("#888888"))
        self.infoLayout.addWidget(self.picCountLabel)
        self.infoLayout.addStretch(1)
        self.infoLayout.addWidget(self.dateLabel)

        # 一键下载按钮 / 打开文件夹按钮
        from qfluentwidgets import FluentIcon as FIF
        from qfluentwidgets import PushButton

        self.downloadBtn = PrimaryPushButton("正在检查状态…", self)
        self.downloadBtn.setFixedHeight(32)
        self.downloadBtn.setEnabled(False)
        self.downloadBtn.clicked.connect(self._on_download_clicked)

        self.openBtn = PushButton(FIF.FOLDER, "打开文件夹", self)
        self.openBtn.setFixedHeight(32)
        self.openBtn.clicked.connect(self._on_open_clicked)
        self.openBtn.setVisible(False)

        self.vbox.addWidget(self.coverLabel)
        self.vbox.addWidget(self.titleLabel)
        self.vbox.addLayout(self.infoLayout)
        self.vbox.addWidget(self.downloadBtn)
        self.vbox.addWidget(self.openBtn)

        total_h = 12 + 250 + 6 + title_h + 6 + 20 + 6 + 32 + 12
        self.setFixedHeight(total_h)

        self.loader = None
        if self.comic.cover_url:
            self._load_cover()

    def _load_cover(self) -> None:
        self._cover_manager.load(self.comic.cover_url, self._set_cover)

    def _set_cover(self, url: str, img: QImage) -> None:
        if url != self.comic.cover_url:
            return
        try:
            if not img.isNull():
                self.coverLabel.setPixmap(QPixmap.fromImage(img))
        except RuntimeError:
            return

    @Slot(str)
    def apply_download_state(self, state: str) -> None:
        """Apply a coordinator snapshot; this method must run on the GUI thread."""
        try:
            resolved_state = ComicCardState(state)
        except ValueError:
            resolved_state = ComicCardState.CHECKING
        self._download_state = resolved_state

        if resolved_state is ComicCardState.QUEUED:
            self.downloadBtn.setVisible(True)
            self.openBtn.setVisible(False)
            self.downloadBtn.setText("已添加到队列")
            self.downloadBtn.setEnabled(False)
        elif resolved_state is ComicCardState.DOWNLOADED:
            self.downloadBtn.setVisible(False)
            self.downloadBtn.setEnabled(False)
            self.openBtn.setVisible(True)
            self.openBtn.setText("已下载 · 打开文件")
            self.openBtn.setEnabled(True)
        elif resolved_state is ComicCardState.MISSING:
            self.downloadBtn.setVisible(True)
            self.openBtn.setVisible(False)
            self.downloadBtn.setText("文件已删除 · 重新下载")
            self.downloadBtn.setEnabled(True)
        elif resolved_state is ComicCardState.FAILED:
            self.downloadBtn.setVisible(True)
            self.openBtn.setVisible(False)
            self.downloadBtn.setText("下载失败 · 重试")
            self.downloadBtn.setEnabled(True)
        elif resolved_state is ComicCardState.AVAILABLE:
            self.downloadBtn.setVisible(True)
            self.openBtn.setVisible(False)
            self.downloadBtn.setText("一键下载")
            self.downloadBtn.setEnabled(True)
        else:
            self.downloadBtn.setVisible(True)
            self.openBtn.setVisible(False)
            self.downloadBtn.setText("正在检查状态…")
            self.downloadBtn.setEnabled(False)

    @property
    def can_queue_download(self) -> bool:
        return self._download_state in {
            ComicCardState.AVAILABLE,
            ComicCardState.MISSING,
            ComicCardState.FAILED,
        }

    def mark_queued(self) -> None:
        """Apply an optimistic queued state while persistence catches up."""
        self.apply_download_state(ComicCardState.QUEUED.value)

    def _on_open_clicked(self) -> None:
        task = self._repository.get_task_by_aid(self.comic.aid)
        path = Path(task.save_path) if task else task_directory(Path(cfg.download_dir), self.comic.title)

        target_path = path
        if not path.exists() and archive_path(path).exists():
            target_path = archive_path(path)

        open_local_path(target_path)

    def setSelected(self, selected: bool) -> None:
        if self._is_selected == selected:
            return
        self._is_selected = selected
        if selected:
            primary = ThemeColor.PRIMARY.color().name()
            self.setStyleSheet(
                f"ComicCard {{ border: 2px solid {primary}; background-color: {primary}1A; border-radius: 8px; }}"
            )
        else:
            self.setStyleSheet("")

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        # Notify parent to handle context menu so we can do bulk actions on all selected items
        parent = self.parent()
        if isinstance(parent, SelectableContainer):
            parent.customContextMenuRequested.emit(self.mapToParent(event.pos()))

    def _on_download_clicked(self) -> None:
        if not self.can_queue_download:
            return
        self.mark_queued()
        self.downloadClicked.emit(self.comic)
