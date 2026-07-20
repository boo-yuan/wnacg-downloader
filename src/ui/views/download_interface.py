from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea, QMenu
from PySide6.QtGui import QContextMenuEvent, QAction
from qfluentwidgets import ProgressBar, TitleLabel, CardWidget, SubtitleLabel, StrongBodyLabel, PushButton, PrimaryPushButton, FluentIcon as FIF, InfoBar, InfoBarPosition
from core.downloader import downloader_manager
from core.models import DownloadTask, TaskStatus
import core.db as db
from ui.components.selectable_container import SelectableContainer
from PySide6.QtGui import QAction, QShortcut, QKeySequence

class DownloadItemCard(CardWidget):
    def __init__(self, task: DownloadTask, parent=None):
        super().__init__(parent)
        self.task = task
        self.setFixedHeight(84)
        self._is_selected = False
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        
        # Top Row: Title and Buttons
        topLayout = QHBoxLayout()
        self.titleLabel = StrongBodyLabel(task.comic.title, self)
        
        self.pauseBtn = PushButton(FIF.PAUSE, '暂停', self)
        self.resumeBtn = PrimaryPushButton(FIF.PLAY, '继续', self)
        self.cancelBtn = PushButton(FIF.DELETE, '取消', self)
        
        self.pauseBtn.clicked.connect(self._on_pause)
        self.resumeBtn.clicked.connect(self._on_resume)
        self.cancelBtn.clicked.connect(self._on_cancel)
        
        topLayout.addWidget(self.titleLabel, 1)
        topLayout.addWidget(self.pauseBtn, 0)
        topLayout.addWidget(self.resumeBtn, 0)
        topLayout.addWidget(self.cancelBtn, 0)
        
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
        
    def _get_status_text(self):
        err_msg = self.task.error_message or ''
        if len(err_msg) > 30:
            err_msg = err_msg[:30] + "..."
            
        m = {
            TaskStatus.PENDING: "等待中...",
            TaskStatus.DOWNLOADING: f"下载中... {self.task.downloaded_images}/{self.task.total_images}",
            TaskStatus.PAUSED: "已暂停",
            TaskStatus.COMPLETED: "已完成",
            TaskStatus.FAILED: f"出错: {err_msg}",
            TaskStatus.CANCELED: "已取消"
        }
        return m.get(self.task.status, str(self.task.status))
        
    def _update_progress_ui(self):
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
            
    def _update_btns(self):
        self.pauseBtn.setVisible(self.task.status in (TaskStatus.PENDING, TaskStatus.DOWNLOADING))
        self.resumeBtn.setVisible(self.task.status in (TaskStatus.PAUSED, TaskStatus.FAILED))
        self.cancelBtn.setVisible(self.task.status != TaskStatus.CANCELED)

    def update_progress(self, downloaded: int, total: int):
        self.task.downloaded_images = downloaded
        self.task.total_images = total
        self._update_progress_ui()
        self.statusLabel.setText(self._get_status_text())
            
    def set_status(self, status: TaskStatus):
        self.task.status = status
        self._update_progress_ui()
        self.statusLabel.setText(self._get_status_text())
        self._update_btns()
        
    def set_error(self, err_msg):
        self.task.status = TaskStatus.FAILED
        self.task.error_message = err_msg
        self._update_progress_ui()
        self.statusLabel.setText(self._get_status_text())
        self._update_btns()

    def _on_pause(self):
        downloader_manager.pause_task(self.task.id)
        
    def _on_resume(self):
        downloader_manager.resume_task(self.task.id)
        
    def _on_cancel(self):
        downloader_manager.cancel_task(self.task.id)
        self.deleteLater()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.task.status in (TaskStatus.PENDING, TaskStatus.DOWNLOADING):
                self._on_pause()
            elif self.task.status in (TaskStatus.PAUSED, TaskStatus.FAILED):
                self._on_resume()
        super().mouseDoubleClickEvent(event)

    def setSelected(self, selected: bool):
        if self._is_selected == selected:
            return
        self._is_selected = selected
        if selected:
            self.setStyleSheet("DownloadItemCard { border: 2px solid #009faa; background-color: rgba(0, 159, 170, 0.1); border-radius: 8px; }")
        else:
            self.setStyleSheet("")
            
    def contextMenuEvent(self, event: QContextMenuEvent):
        self.parent().customContextMenuRequested.emit(self.mapToParent(event.pos()))

class DownloadInterface(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("DownloadInterface")
        
        self.vbox = QVBoxLayout(self)
        self.vbox.setContentsMargins(24, 24, 24, 24)
        
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
        
        self._load_existing_tasks()
        
        downloader_manager.signals.task_added.connect(self._on_task_added)
        downloader_manager.signals.task_progress.connect(self._on_task_progress)
        downloader_manager.signals.task_status_changed.connect(self._on_task_status_changed)
        downloader_manager.signals.task_error.connect(self._on_task_error)
        
    def _load_existing_tasks(self):
        tasks = db.get_all_tasks()
        for task in tasks:
            self._on_task_added(task)
            
    def _on_task_added(self, task: DownloadTask):
        if task.id in self.task_cards:
            return
        card = DownloadItemCard(task, self.scrollWidget)
        self.listLayout.addWidget(card)
        self.task_cards[task.id] = card
        
    def _on_task_progress(self, task_id, downloaded, total):
        card = self.task_cards.get(task_id)
        if card:
            card.update_progress(downloaded, total)
            
    def _on_task_status_changed(self, task_id, new_status: TaskStatus):
        card = self.task_cards.get(task_id)
        if card:
            if new_status == TaskStatus.CANCELED:
                del self.task_cards[task_id]
            else:
                card.set_status(new_status)
            
    def _on_task_error(self, task_id, err_msg):
        card = self.task_cards.get(task_id)
        if card:
            card.set_error(err_msg)

    def _show_context_menu(self, pos):
        selected_items = self.scrollWidget.get_selected_items()
        if not selected_items:
            return
            
        menu = QMenu(self)
        
        action_resume = QAction(f"开始/继续下载 ({len(selected_items)}项)", self)
        action_resume.triggered.connect(lambda: self._bulk_resume(selected_items))
        menu.addAction(action_resume)
        
        action_pause = QAction(f"暂停下载", self)
        action_pause.triggered.connect(lambda: self._bulk_pause(selected_items))
        menu.addAction(action_pause)
        
        action_cancel = QAction(f"取消任务", self)
        action_cancel.triggered.connect(lambda: self._bulk_cancel(selected_items))
        menu.addAction(action_cancel)
        
        menu.addSeparator()
        action_deselect = QAction("取消选中", self)
        action_deselect.triggered.connect(self.scrollWidget.clear_selection)
        menu.addAction(action_deselect)
        
        global_pos = self.scrollWidget.mapToGlobal(pos)
        menu.exec(global_pos)
        
    def _bulk_resume(self, items):
        for item in items:
            if hasattr(item, 'task') and item.task.status in (TaskStatus.PAUSED, TaskStatus.FAILED, TaskStatus.PENDING):
                downloader_manager.resume_task(item.task.id)
        self.scrollWidget.clear_selection()
                
    def _bulk_pause(self, items):
        for item in items:
            if hasattr(item, 'task') and item.task.status in (TaskStatus.PENDING, TaskStatus.DOWNLOADING):
                downloader_manager.pause_task(item.task.id)
        self.scrollWidget.clear_selection()
                
    def _bulk_cancel(self, items):
        for item in items:
            if hasattr(item, 'task') and item.task.status != TaskStatus.CANCELED:
                downloader_manager.cancel_task(item.task.id)
                item.deleteLater()
        self.scrollWidget.clear_selection()
