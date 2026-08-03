"""Download queue presentation and user commands."""
# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false

from pathlib import Path
from typing import cast

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QAction, QContextMenuEvent, QKeySequence, QMouseEvent, QResizeEvent, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QScrollArea, QVBoxLayout, QWidget
from qfluentwidgets import (
    Action,
    BodyLabel,
    CardWidget,
    CheckBox,
    CommandBar,
    InfoBar,
    InfoBarPosition,
    MessageBoxBase,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
    TitleLabel,
)
from qfluentwidgets import FluentIcon as FIF

from wnacg.application.downloader import DownloaderWorker
from wnacg.application.file_paths import archive_path
from wnacg.application.ports import TaskRepository
from wnacg.domain.models import CANCELLABLE_TASK_STATUSES, DownloadTask, TaskStatus
from wnacg.infrastructure.config import cfg
from wnacg.ui.components.selectable_container import SelectableContainer
from wnacg.ui.open_path import open_local_path


class CancelPromptDialog(MessageBoxBase):
    def __init__(self, count: int = 1, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.titleLabel = SubtitleLabel("确认取消任务", self)

        text = (
            f"您确定要取消选中的 {count} 个任务吗？\n注意：取消后任务记录将从列表中彻底消失。"
            if count > 1
            else "您确定要取消这个任务吗？\n注意：取消后任务记录将从列表中彻底消失。"
        )
        self.contentLabel = BodyLabel(text, self)

        self.deleteFilesCheckbox = CheckBox("同时彻底删除已下载到本地的残余文件", self)
        self.deleteFilesCheckbox.setChecked(cfg.delete_files_on_cancel)

        self.checkbox = CheckBox("记住本次选择，下次直接执行不再弹窗", self)

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.contentLabel)
        self.viewLayout.addWidget(self.deleteFilesCheckbox)
        self.viewLayout.addWidget(self.checkbox)

        self.viewLayout.setSpacing(12)
        self.viewLayout.setContentsMargins(24, 24, 24, 24)

        self.yesButton.setText("确定取消")
        self.cancelButton.setText("暂不取消")


class DownloadItemCard(CardWidget):
    def __init__(self, task: DownloadTask, downloader: DownloaderWorker, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.task = task
        self._downloader = downloader
        self.setFixedHeight(84)
        self._is_selected = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)

        # Top Row: Title and Buttons
        topLayout = QHBoxLayout()
        self.titleLabel = StrongBodyLabel(task.comic.title, self)

        self.pauseBtn = PushButton(FIF.PAUSE, "暂停", self)
        self.resumeBtn = PrimaryPushButton(FIF.PLAY, "继续", self)
        self.cancelBtn = PushButton(FIF.DELETE, "取消", self)
        self.openBtn = PushButton(FIF.FOLDER, "打开文件夹", self)

        self.pauseBtn.clicked.connect(self._on_pause)
        self.resumeBtn.clicked.connect(self._on_resume)
        self.cancelBtn.clicked.connect(self._on_cancel)
        self.openBtn.clicked.connect(self.open_result)

        topLayout.addWidget(self.titleLabel, 1)
        topLayout.addWidget(self.pauseBtn, 0)
        topLayout.addWidget(self.resumeBtn, 0)
        topLayout.addWidget(self.cancelBtn, 0)
        topLayout.addWidget(self.openBtn, 0)

        layout.addLayout(topLayout)

        # Bottom Row: Progress Bar and Status
        bottomLayout = QHBoxLayout()
        self.progressBar = ProgressBar(self)
        self.progressBar.setRange(0, 100)

        self.statusLabel = QLabel(self._get_status_text(), self)
        self.statusLabel.setStyleSheet("color: #666;")

        bottomLayout.addWidget(self.progressBar, 1)
        bottomLayout.addWidget(self.statusLabel, 0)

        layout.addLayout(bottomLayout)

        self._update_progress_ui()
        self._update_btns()

    def _get_status_text(self) -> str:
        err_msg = self.task.error_message or ""
        if len(err_msg) > 30:
            err_msg = err_msg[:30] + "..."

        m = {
            TaskStatus.PENDING: "等待中...",
            TaskStatus.DOWNLOADING: f"下载中... {self.task.downloaded_images}/{self.task.total_images}",
            TaskStatus.PAUSED: "已暂停",
            TaskStatus.COMPLETED: "已完成",
            TaskStatus.FAILED: f"出错: {err_msg}",
            TaskStatus.MISSING: "文件缺失，可重新下载",
            TaskStatus.CANCELED: "已取消",
        }
        return m.get(self.task.status, str(self.task.status))

    def _update_progress_ui(self) -> None:
        if self.task.total_images > 0:
            val = int(self.task.downloaded_images / self.task.total_images * 100)
            self.progressBar.setValue(val)
        else:
            self.progressBar.setValue(0)

        if self.task.status == TaskStatus.FAILED:
            self.progressBar.error()
        elif self.task.status == TaskStatus.PAUSED:
            self.progressBar.pause()
        else:
            self.progressBar.resume()

    def _update_btns(self) -> None:
        self.pauseBtn.setVisible(self.task.status in (TaskStatus.PENDING, TaskStatus.DOWNLOADING))
        self.resumeBtn.setVisible(self.task.status in (TaskStatus.PAUSED, TaskStatus.FAILED, TaskStatus.MISSING))
        self.cancelBtn.setVisible(self.task.status not in (TaskStatus.CANCELED, TaskStatus.COMPLETED))
        self.openBtn.setVisible(self.task.status == TaskStatus.COMPLETED)

    def update_progress(self, downloaded: int, total: int) -> None:
        self.task.set_progress(downloaded, total)
        self._update_progress_ui()
        self.statusLabel.setText(self._get_status_text())

    def set_status(self, status: TaskStatus) -> None:
        self.task.status = status
        self._update_progress_ui()
        self.statusLabel.setText(self._get_status_text())
        self._update_btns()

    def set_error(self, err_msg: str) -> None:
        self.task.status = TaskStatus.FAILED
        self.task.error_message = err_msg
        self._update_progress_ui()
        self.statusLabel.setText(self._get_status_text())
        self._update_btns()

    def _on_pause(self) -> None:
        self._downloader.pause_task(self.task.id)

    def _on_resume(self) -> None:
        self._downloader.resume_task(self.task.id)

    def _on_cancel(self) -> None:
        if cfg.show_cancel_prompt:
            w = CancelPromptDialog(1, self.window())
            if not w.exec():
                return
            cfg.delete_files_on_cancel = w.deleteFilesCheckbox.isChecked()
            if w.checkbox.isChecked():
                cfg.show_cancel_prompt = False
            cfg.save()

        self._downloader.cancel_task(self.task.id)

    def open_result(self) -> None:
        path = Path(self.task.save_path)

        target_path = path
        if not path.exists() and archive_path(path).exists():
            target_path = archive_path(path)

        open_local_path(target_path)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if self.task.status in (TaskStatus.PENDING, TaskStatus.DOWNLOADING):
                self._on_pause()
            elif self.task.status in (TaskStatus.PAUSED, TaskStatus.FAILED, TaskStatus.MISSING):
                self._on_resume()
            elif self.task.status == TaskStatus.COMPLETED:
                self.open_result()
        super().mouseDoubleClickEvent(event)

    def setSelected(self, selected: bool) -> None:
        if self._is_selected == selected:
            return
        self._is_selected = selected
        if selected:
            self.setStyleSheet(
                "DownloadItemCard { border: 2px solid #009faa; "
                "background-color: rgba(0, 159, 170, 0.1); border-radius: 8px; }"
            )
        else:
            self.setStyleSheet("")

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        parent = self.parent()
        if isinstance(parent, SelectableContainer):
            parent.customContextMenuRequested.emit(self.mapToParent(event.pos()))


class DownloadInterface(QWidget):
    def __init__(
        self,
        downloader: DownloaderWorker,
        repository: TaskRepository,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent=parent)
        self._downloader = downloader
        self._repository = repository
        self.setObjectName("DownloadInterface")

        self.vbox = QVBoxLayout(self)
        self.vbox.setContentsMargins(24, 24, 24, 24)

        self.commandBar = CommandBar(self)
        self.commandBar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)

        self.hintAction = Action(FIF.HELP, "操作提示", self)
        self.hintAction.triggered.connect(self._show_hint)
        self.commandBar.addAction(self.hintAction)
        self.commandBar.addSeparator()

        self.selectAllAction = Action(FIF.CHECKBOX, "全选", self)
        self.selectAllAction.triggered.connect(lambda: self.scrollWidget.select_all())
        self.commandBar.addAction(self.selectAllAction)

        self.startSelectedAction = Action(FIF.PLAY, "开始任务", self)
        self.startSelectedAction.triggered.connect(self._start_selected)
        self.commandBar.addAction(self.startSelectedAction)

        self.commandBar.addSeparator()

        self.startAllAction = Action(FIF.PLAY, "全部开始", self)
        self.startAllAction.triggered.connect(self._start_all)
        self.commandBar.addAction(self.startAllAction)

        self.pauseAllAction = Action(FIF.PAUSE, "全部暂停", self)
        self.pauseAllAction.triggered.connect(self._pause_all)
        self.commandBar.addAction(self.pauseAllAction)

        self.cancelAllAction = Action(FIF.CANCEL, "全部取消", self)
        self.cancelAllAction.triggered.connect(self._cancel_all)
        self.commandBar.addAction(self.cancelAllAction)

        self.clearCompletedAction = Action(FIF.DELETE, "清除已完成记录", self)
        self.clearCompletedAction.setToolTip("仅从列表中移除记录，您的漫画文件非常安全，不会被删除")
        self.clearCompletedAction.triggered.connect(self._clear_completed)
        self.commandBar.addAction(self.clearCompletedAction)

        self.vbox.addWidget(self.commandBar)

        self.scrollArea = QScrollArea(self)
        self.scrollArea.setWidgetResizable(True)
        self.scrollArea.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self.scrollWidget = SelectableContainer()
        self.scrollWidget.setStyleSheet("background: transparent;")
        self.scrollWidget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.scrollWidget.customContextMenuRequested.connect(self._show_context_menu)

        self.listLayout = QVBoxLayout(self.scrollWidget)
        self.listLayout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.scrollArea.setWidget(self.scrollWidget)
        self.vbox.addWidget(self.scrollArea)

        # Shortcut for Ctrl+A
        self.shortcut_select_all = QShortcut(QKeySequence("Ctrl+A"), self)
        self.shortcut_select_all.activated.connect(self.scrollWidget.select_all)

        self.task_cards = {}

        self._init_empty_state()

        self._load_existing_tasks()

        downloader.signals.task_added.connect(self._on_task_added)
        downloader.signals.task_progress.connect(self._on_task_progress)
        downloader.signals.task_status_changed.connect(self._on_task_status_changed)
        downloader.signals.task_error.connect(self._on_task_error)
        downloader.signals.task_deletion_result.connect(self._on_task_deletion_result)

        self._update_empty_state()

        # 返回顶部悬浮按钮
        from qfluentwidgets import PrimaryToolButton, ThemeColor, setFont

        self.backToTopBtn = PrimaryToolButton(FIF.UP, self)
        setFont(self.backToTopBtn)
        self.backToTopBtn.setFixedSize(40, 40)
        self.backToTopBtn.hide()
        primary = ThemeColor.PRIMARY.color().name()
        self.backToTopBtn.setStyleSheet(
            f"PrimaryToolButton {{ border-radius: 20px; background-color: {primary}; border: none; }}"
        )
        self.backToTopBtn.clicked.connect(lambda: self.scrollArea.verticalScrollBar().setValue(0))
        self.scrollArea.verticalScrollBar().valueChanged.connect(self._on_scroll)

    def _show_hint(self) -> None:
        InfoBar.info(
            title="💡 操作提示",
            content=(
                "支持鼠标框选 / Shift连选 / Ctrl+A全选\n右键卡片可进行批量操作\n"
                "双击卡片可快速暂停/恢复或打开已完成文件夹"
            ),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=5000,
            parent=self,
        )

    def _on_scroll(self, value: int) -> None:
        if value > 300:
            self.backToTopBtn.show()
        else:
            self.backToTopBtn.hide()

    def resizeEvent(self, e: QResizeEvent) -> None:
        super().resizeEvent(e)
        self.backToTopBtn.move(self.width() - 80, self.height() - 100)

    def _start_selected(self) -> None:
        selected = [cast(DownloadItemCard, item) for item in self.scrollWidget.get_selected_items()]
        if not selected:
            return
        task_ids = [
            c.task.id
            for c in selected
            if c.task.status in (TaskStatus.PAUSED, TaskStatus.FAILED, TaskStatus.MISSING, TaskStatus.PENDING)
        ]
        if task_ids:
            self._downloader.resume_tasks(task_ids)

    def _init_empty_state(self) -> None:
        self.emptyWidget = QWidget(self)
        emptyLayout = QVBoxLayout(self.emptyWidget)
        emptyLayout.setSpacing(12)

        from pathlib import Path

        from PySide6.QtGui import QPixmap
        from qfluentwidgets import SubtitleLabel

        self.emptyImage = QLabel(self)
        icon_path = Path(__file__).resolve().parents[2] / "resource" / "icon.png"
        pixmap = QPixmap(str(icon_path))
        if not pixmap.isNull():
            pixmap = pixmap.scaled(
                100, 100, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
            self.emptyImage.setPixmap(pixmap)
        self.emptyImage.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.emptyTitle = TitleLabel("暂无下载任务", self)
        self.emptyTitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.emptyTitle.setStyleSheet("font-size: 28px; font-weight: 900;")

        self.emptySubtitle = SubtitleLabel("暂无下载任务 / 你的下载列表很干净，快去主页搜索喜欢的漫画吧！", self)
        self.emptySubtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.emptySubtitle.setStyleSheet("color: #888888; font-size: 15px;")

        emptyLayout.addStretch(1)
        emptyLayout.addWidget(self.emptyImage)
        emptyLayout.addWidget(self.emptyTitle)
        emptyLayout.addWidget(self.emptySubtitle)
        emptyLayout.addStretch(2)

        self.vbox.addWidget(self.emptyWidget, 1)

    def _update_empty_state(self) -> None:
        has_tasks = len(self.task_cards)
        self.commandBar.setVisible(bool(has_tasks))
        self.scrollArea.setVisible(bool(has_tasks))
        self.emptyWidget.setVisible(not has_tasks)

    def _load_existing_tasks(self) -> None:
        tasks = self._repository.get_all_tasks()
        for task in tasks:
            self._on_task_added(task)

    def _on_task_added(self, task: DownloadTask) -> None:
        if task.id in self.task_cards:
            return
        card = DownloadItemCard(task, self._downloader, self.scrollWidget)
        self.listLayout.addWidget(card)
        self.task_cards[task.id] = card
        self._update_empty_state()

    def _on_task_progress(self, task_id: str, downloaded: int, total: int) -> None:
        card = self.task_cards.get(task_id)
        if card:
            card.update_progress(downloaded, total)

    def _on_task_status_changed(self, task_id: str, new_status: TaskStatus) -> None:
        card = self.task_cards.get(task_id)
        if card:
            card.set_status(new_status)

    def _on_task_deletion_result(self, task_id: str, succeeded: bool, error: str) -> None:
        card = self.task_cards.get(task_id)
        if succeeded:
            if card is not None:
                self.task_cards.pop(task_id, None)
                self.listLayout.removeWidget(card)
                card.deleteLater()
                self._update_empty_state()
            return
        InfoBar.error(
            "删除失败",
            f"任务 {task_id} 未删除：{error}",
            parent=self.window(),
            position=InfoBarPosition.TOP_RIGHT,
        )

    def _on_task_error(self, task_id: str, err_msg: str) -> None:
        card = self.task_cards.get(task_id)
        if card:
            card.set_error(err_msg)

    def _show_context_menu(self, pos: QPoint) -> None:
        selected_items = [cast(DownloadItemCard, item) for item in self.scrollWidget.get_selected_items()]
        target_item = self.scrollWidget.get_item_at(pos)
        target_card = cast(DownloadItemCard | None, target_item)

        if not selected_items:
            if target_card:
                selected_items = [target_card]
            else:
                return
        elif target_card and target_card not in selected_items:
            self.scrollWidget.clear_selection()
            target_card.setSelected(True)
            selected_items = [target_card]

        menu = QMenu(self)

        action_select_all = QAction("全选", self)
        action_select_all.triggered.connect(self.scrollWidget.select_all)
        menu.addAction(action_select_all)

        menu.addSeparator()

        if len(selected_items) == 1:
            action_open = QAction("打开所在文件夹", self)
            action_open.triggered.connect(selected_items[0].open_result)
            menu.addAction(action_open)
            menu.addSeparator()

        action_resume = QAction(f"开始/继续下载 ({len(selected_items)}项)", self)
        action_resume.triggered.connect(lambda: self._bulk_resume(selected_items))
        menu.addAction(action_resume)

        action_pause = QAction("暂停下载", self)
        action_pause.triggered.connect(lambda: self._bulk_pause(selected_items))
        menu.addAction(action_pause)

        action_cancel = QAction("取消任务", self)
        action_cancel.triggered.connect(lambda: self._bulk_cancel(selected_items))
        menu.addAction(action_cancel)

        menu.addSeparator()
        action_deselect = QAction("取消选中", self)
        action_deselect.triggered.connect(self.scrollWidget.clear_selection)
        menu.addAction(action_deselect)

        global_pos = self.scrollWidget.mapToGlobal(pos)
        menu.exec(global_pos)

    def _bulk_resume(self, items: list[DownloadItemCard]) -> None:
        task_ids = [
            item.task.id
            for item in items
            if hasattr(item, "task")
            and item.task.status in (TaskStatus.PAUSED, TaskStatus.FAILED, TaskStatus.MISSING, TaskStatus.PENDING)
        ]
        if task_ids:
            self._downloader.resume_tasks(task_ids)
        self.scrollWidget.clear_selection()

    def _bulk_pause(self, items: list[DownloadItemCard]) -> None:
        task_ids = [
            item.task.id
            for item in items
            if hasattr(item, "task") and item.task.status in (TaskStatus.PENDING, TaskStatus.DOWNLOADING)
        ]
        if task_ids:
            self._downloader.pause_tasks(task_ids)
        self.scrollWidget.clear_selection()

    def _bulk_cancel(self, items: list[DownloadItemCard]) -> None:
        valid_items = [
            item for item in items if hasattr(item, "task") and item.task.status in CANCELLABLE_TASK_STATUSES
        ]
        if not valid_items:
            return

        if cfg.show_cancel_prompt:
            w = CancelPromptDialog(len(valid_items), self.window())
            if not w.exec():
                return
            cfg.delete_files_on_cancel = w.deleteFilesCheckbox.isChecked()
            if w.checkbox.isChecked():
                cfg.show_cancel_prompt = False
            cfg.save()

        task_ids = [item.task.id for item in valid_items]

        if task_ids:
            self._downloader.cancel_tasks(task_ids)
        self.scrollWidget.clear_selection()

    def _start_all(self) -> None:
        task_ids = [
            card.task.id
            for card in self.task_cards.values()
            if card.task.status in (TaskStatus.PAUSED, TaskStatus.FAILED, TaskStatus.MISSING, TaskStatus.PENDING)
        ]
        if task_ids:
            self._downloader.resume_tasks(task_ids)
            InfoBar.success(
                "🚀 批量启动",
                "已唤醒所有待命的任务，火力全开！",
                parent=self.window(),
                position=InfoBarPosition.TOP_RIGHT,
            )

    def _pause_all(self) -> None:
        task_ids = [
            card.task.id
            for card in self.task_cards.values()
            if card.task.status in (TaskStatus.PENDING, TaskStatus.DOWNLOADING)
        ]
        if task_ids:
            self._downloader.pause_tasks(task_ids)
            InfoBar.warning(
                "⏸️ 紧急刹车", "正在下载的任务已全部暂停", parent=self.window(), position=InfoBarPosition.TOP_RIGHT
            )

    def _clear_completed(self) -> None:
        to_remove = [task_id for task_id, card in self.task_cards.items() if card.task.status == TaskStatus.COMPLETED]
        if to_remove:
            self._downloader.delete_tasks(to_remove, delete_files=False)
        if to_remove:
            InfoBar.success(
                "🧹 正在清理",
                "完成记录将在持久化删除成功后从列表移除（文件不会删除）",
                parent=self.window(),
                position=InfoBarPosition.TOP_RIGHT,
            )

    def _cancel_all(self) -> None:
        valid_items = [card for card in self.task_cards.values() if card.task.status in CANCELLABLE_TASK_STATUSES]
        if not valid_items:
            return

        if cfg.show_cancel_prompt:
            w = CancelPromptDialog(len(valid_items), self.window())
            if not w.exec():
                return
            cfg.delete_files_on_cancel = w.deleteFilesCheckbox.isChecked()
            if w.checkbox.isChecked():
                cfg.show_cancel_prompt = False
            cfg.save()

        to_remove = [card.task.id for card in valid_items]

        if to_remove:
            self._downloader.cancel_tasks(to_remove)
