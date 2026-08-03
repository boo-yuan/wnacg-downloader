"""Search-result card and bounded asynchronous task-state lookup."""

import re
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot
from PySide6.QtGui import QColor, QContextMenuEvent, QImage, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout
from qfluentwidgets import CaptionLabel, ElevatedCardWidget, PrimaryPushButton, ThemeColor

from wnacg.application.file_paths import archive_path, task_directory
from wnacg.domain.models import Comic, TaskStatus
from wnacg.infrastructure.config import cfg
from wnacg.infrastructure.database import task_repository as db
from wnacg.ui.components.cover_manager import cover_manager
from wnacg.ui.components.selectable_container import SelectableContainer
from wnacg.ui.open_path import open_local_path


class _StateSignals(QObject):
    finished = Signal(str, bool)


class _StateWorker(QRunnable):
    """Bounded-pool task-state lookup used by gallery cards."""

    def __init__(self, comic: Comic) -> None:
        super().__init__()
        self.comic = comic
        self.signals = _StateSignals()

    @Slot()
    def run(self) -> None:
        task = db.get_task_by_aid(self.comic.aid)
        path = (
            Path(task.save_path) if task else task_directory(Path(cfg.download_dir), self.comic.title, self.comic.aid)
        )
        on_disk = path.is_dir() or archive_path(path).is_file()
        if task and task.status in (TaskStatus.PENDING, TaskStatus.DOWNLOADING, TaskStatus.PAUSED):
            state = "queued"
        elif task and task.status == TaskStatus.COMPLETED:
            state = "downloaded"
        else:
            expected_match = re.search(r"\d+", self.comic.pic_count)
            if path.is_dir() and expected_match:
                expected = int(expected_match.group())
                on_disk = sum(file.is_file() for file in path.iterdir()) >= expected > 0
            state = "downloaded" if on_disk else "download"
        self.signals.finished.emit(state, task is not None)


_STATE_POOL = QThreadPool()
_STATE_POOL.setMaxThreadCount(4)


class ComicCard(ElevatedCardWidget):
    downloadClicked = Signal(Comic)

    def __init__(self, comic: Comic, parent=None):
        super().__init__(parent)
        self.comic = comic
        self.setFixedWidth(220)

        self.vbox = QVBoxLayout(self)
        self.vbox.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.vbox.setSpacing(6)
        self.vbox.setContentsMargins(12, 12, 12, 12)

        self._is_selected = False

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

    def _load_cover(self):
        cover_manager.load(self.comic.cover_url, self._set_cover)

    def _set_cover(self, url: str, img: QImage):
        if url != self.comic.cover_url:
            return
        try:
            if not img.isNull():
                self.coverLabel.setPixmap(QPixmap.fromImage(img))
        except RuntimeError:
            pass

    _state_updated = Signal(str, bool)

    def update_download_state(self):
        worker = _StateWorker(self.comic)
        worker.signals.finished.connect(self._state_updated.emit)
        _STATE_POOL.start(worker)

    def _apply_download_state(self, state: str, has_task: bool):
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

    def _on_open_clicked(self):
        task = db.get_task_by_aid(self.comic.aid)
        path = (
            Path(task.save_path) if task else task_directory(Path(cfg.download_dir), self.comic.title, self.comic.aid)
        )

        target_path = path
        if not path.exists() and archive_path(path).exists():
            target_path = archive_path(path)

        if not target_path.exists():
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path = target_path.parent

        open_local_path(target_path)

    def setSelected(self, selected: bool):
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

    def contextMenuEvent(self, event: QContextMenuEvent):
        # Notify parent to handle context menu so we can do bulk actions on all selected items
        parent = self.parent()
        if isinstance(parent, SelectableContainer):
            parent.customContextMenuRequested.emit(self.mapToParent(event.pos()))

    def _on_download_clicked(self):
        self.downloadBtn.setText("已添加队列")
        self.downloadBtn.setEnabled(False)
        self.downloadClicked.emit(self.comic)
