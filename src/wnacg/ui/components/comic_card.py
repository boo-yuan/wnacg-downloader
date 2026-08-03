"""Search-result card and bounded asynchronous task-state lookup."""
# pyright: reportUnknownMemberType=false

import re
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot
from PySide6.QtGui import QColor, QContextMenuEvent, QImage, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import CaptionLabel, ElevatedCardWidget, PrimaryPushButton, ThemeColor

from wnacg.application.file_paths import archive_path, task_directory
from wnacg.application.ports import TaskRepository
from wnacg.domain.models import Comic, TaskStatus
from wnacg.infrastructure.config import cfg
from wnacg.ui.components.cover_manager import CoverManagerClass
from wnacg.ui.components.selectable_container import SelectableContainer
from wnacg.ui.open_path import open_local_path


class _StateSignals(QObject):
    finished = Signal(int, str, bool)


class _StateWorker(QRunnable):
    """Bounded-pool task-state lookup used by gallery cards."""

    def __init__(self, comic: Comic, generation: int, repository: TaskRepository) -> None:
        super().__init__()
        self.comic = comic
        self.generation = generation
        self._repository = repository
        self.signals = _StateSignals()

    @Slot()
    def run(self) -> None:
        task = self._repository.get_task_by_aid(self.comic.aid)
        path = (
            Path(task.save_path) if task else task_directory(Path(cfg.download_dir), self.comic.title, self.comic.aid)
        )
        on_disk = path.is_dir() or archive_path(path).is_file()
        if task and task.status in (TaskStatus.PENDING, TaskStatus.DOWNLOADING, TaskStatus.PAUSED):
            state = "queued"
        elif task and task.status == TaskStatus.COMPLETED:
            state = "downloaded" if on_disk else "download"
        else:
            expected_match = re.search(r"\d+", self.comic.pic_count)
            if path.is_dir() and expected_match:
                expected = int(expected_match.group())
                image_extensions = {".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".webp"}
                managed_image_count = sum(
                    file.is_file() and file.suffix.lower() in image_extensions for file in path.iterdir()
                )
                on_disk = managed_image_count >= expected > 0
            state = "downloaded" if on_disk else "download"
        self.signals.finished.emit(self.generation, state, task is not None)


class ComicCard(ElevatedCardWidget):
    downloadClicked = Signal(Comic)

    def __init__(
        self,
        comic: Comic,
        repository: TaskRepository,
        cover_manager: CoverManagerClass,
        state_pool: QThreadPool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.comic = comic
        self._repository = repository
        self._cover_manager = cover_manager
        self._state_pool = state_pool
        self.setFixedWidth(220)

        self.vbox = QVBoxLayout(self)
        self.vbox.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.vbox.setSpacing(6)
        self.vbox.setContentsMargins(12, 12, 12, 12)

        self._is_selected = False
        self._state_generation = 0

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

        self.downloadBtn = PrimaryPushButton("一键下载", self)
        self.downloadBtn.setFixedHeight(32)
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

        self._state_updated.connect(self._apply_download_state)
        self.update_download_state()

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

    _state_updated = Signal(int, str, bool)

    def update_download_state(self) -> None:
        self._state_generation += 1
        worker = _StateWorker(self.comic, self._state_generation, self._repository)
        worker.signals.finished.connect(self._state_updated.emit)
        self._state_pool.start(worker)

    def _apply_download_state(self, generation: int, state: str, has_task: bool) -> None:
        if generation != self._state_generation:
            return
        if state == "queued":
            self.downloadBtn.setVisible(True)
            self.openBtn.setVisible(False)
            self.downloadBtn.setText("已在队列")
            self.downloadBtn.setEnabled(False)
        elif state == "downloaded":
            self.downloadBtn.setVisible(False)
            self.openBtn.setVisible(True)
        else:
            self.downloadBtn.setVisible(True)
            self.openBtn.setVisible(False)
            self.downloadBtn.setText("一键下载" if not has_task else "重新下载")
            self.downloadBtn.setEnabled(True)

    def _on_open_clicked(self) -> None:
        task = self._repository.get_task_by_aid(self.comic.aid)
        path = (
            Path(task.save_path) if task else task_directory(Path(cfg.download_dir), self.comic.title, self.comic.aid)
        )

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
        self.downloadBtn.setText("已添加队列")
        self.downloadBtn.setEnabled(False)
        self.downloadClicked.emit(self.comic)
