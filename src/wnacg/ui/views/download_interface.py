from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QContextMenuEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QLabel, QMenu, QScrollArea, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    CheckBox,
    MessageBoxBase,
    PrimaryPushButton,
    ProgressBar,
    PushButton,
    StrongBodyLabel,
    SubtitleLabel,
    TitleLabel,
    ToolButton,
    CommandBar,
    Action,
    InfoBar,
    InfoBarPosition,
)
from qfluentwidgets import FluentIcon as FIF

import core.db as db
from core.config import cfg
from core.downloader import downloader_manager
from core.models import DownloadTask, TaskStatus
from ui.components.selectable_container import SelectableContainer


class CancelPromptDialog(MessageBoxBase):
    def __init__(self, count=1, parent=None):
        super().__init__(parent)
        self.titleLabel = SubtitleLabel("确认取消任务", self)
        
        text = f"您确定要取消选中的 {count} 个任务吗？\n注意：取消后任务记录将从列表中彻底消失。" if count > 1 else "您确定要取消这个任务吗？\n注意：取消后任务记录将从列表中彻底消失。"
        self.contentLabel = BodyLabel(text, self)
        
        self.deleteFilesCheckbox = CheckBox("同时彻底删除已下载到本地的残余文件", self)
        self.deleteFilesCheckbox.setChecked(cfg.delete_files_on_cancel)
        self.deleteFilesCheckbox.stateChanged.connect(self._on_delete_files_changed)
        
        self.checkbox = CheckBox("记住本次选择，下次直接执行不再弹窗", self)
        
        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.contentLabel)
        self.viewLayout.addWidget(self.deleteFilesCheckbox)
        self.viewLayout.addWidget(self.checkbox)
        
        self.viewLayout.setSpacing(12)
        self.viewLayout.setContentsMargins(24, 24, 24, 24)
        
        self.yesButton.setText("确定取消")
        self.cancelButton.setText("暂不取消")
        
    def _on_delete_files_changed(self, state):
        cfg.delete_files_on_cancel = (state == Qt.CheckState.Checked.value)
        cfg.save()

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
        self.openBtn = PushButton(FIF.FOLDER, '打开文件夹', self)
        
        self.pauseBtn.clicked.connect(self._on_pause)
        self.resumeBtn.clicked.connect(self._on_resume)
        self.cancelBtn.clicked.connect(self._on_cancel)
        self.openBtn.clicked.connect(self._on_open)
        
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
        self.cancelBtn.setVisible(self.task.status not in (TaskStatus.CANCELED, TaskStatus.COMPLETED))
        self.openBtn.setVisible(self.task.status == TaskStatus.COMPLETED)

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
        if cfg.show_cancel_prompt:
            w = CancelPromptDialog(1, self.window())
            if not w.exec():
                return
            if w.checkbox.isChecked():
                cfg.show_cancel_prompt = False
                cfg.save()
                
        downloader_manager.cancel_task(self.task.id)
        self.deleteLater()

    def _on_open(self):
        import os
        import platform
        from pathlib import Path
        path = Path(self.task.save_path)
        
        target_path = path
        if not path.exists() and path.with_suffix('.zip').exists():
            target_path = path.with_suffix('.zip')
            
        if not target_path.exists():
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path = target_path.parent
            
        if platform.system() == 'Windows':
            if target_path.is_file():
                import subprocess
                subprocess.run(['explorer', '/select,', str(target_path)])
            else:
                os.startfile(str(target_path))
        elif platform.system() == 'Darwin':
            import subprocess
            subprocess.run(['open', '-R' if target_path.is_file() else '', str(target_path)])
        else:
            import subprocess
            subprocess.run(['xdg-open', str(target_path.parent if target_path.is_file() else target_path)])

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.task.status in (TaskStatus.PENDING, TaskStatus.DOWNLOADING):
                self._on_pause()
            elif self.task.status in (TaskStatus.PAUSED, TaskStatus.FAILED):
                self._on_resume()
            elif self.task.status == TaskStatus.COMPLETED:
                self._on_open()
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
        
        self.commandBar = CommandBar(self)
        self.commandBar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        
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
        
        downloader_manager.signals.task_added.connect(self._on_task_added)
        downloader_manager.signals.task_progress.connect(self._on_task_progress)
        downloader_manager.signals.task_status_changed.connect(self._on_task_status_changed)
        downloader_manager.signals.task_error.connect(self._on_task_error)
        
        self._update_empty_state()
        
        # 返回顶部悬浮按钮
        from qfluentwidgets import PrimaryToolButton, ThemeColor, setFont
        self.backToTopBtn = PrimaryToolButton(FIF.UP, self)
        setFont(self.backToTopBtn)
        self.backToTopBtn.setFixedSize(40, 40)
        self.backToTopBtn.hide()
        primary = ThemeColor.PRIMARY.color().name()
        self.backToTopBtn.setStyleSheet(f"PrimaryToolButton {{ border-radius: 20px; background-color: {primary}; border: none; }}")
        self.backToTopBtn.clicked.connect(lambda: self.scrollArea.verticalScrollBar().setValue(0))
        self.scrollArea.verticalScrollBar().valueChanged.connect(self._on_scroll)
        
    def _show_hint(self):
        InfoBar.info(
            title="💡 操作提示",
            content="支持鼠标框选 / Shift连选 / Ctrl+A全选\n右键卡片可进行批量操作\n双击卡片可快速暂停/恢复或打开已完成文件夹",
            orient=Qt.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=5000,
            parent=self
        )
        
    def _on_scroll(self, value):
        if value > 300:
            self.backToTopBtn.show()
        else:
            self.backToTopBtn.hide()
            
    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.backToTopBtn.move(self.width() - 80, self.height() - 100)
        
    def _start_selected(self):
        selected = self.scrollWidget.get_selected_items()
        if not selected:
            return
        task_ids = [c.task.id for c in selected if c.task.status in (TaskStatus.PAUSED, TaskStatus.FAILED, TaskStatus.PENDING)]
        if task_ids:
            downloader_manager.resume_tasks(task_ids)
        
    def _init_empty_state(self):
        self.emptyWidget = QWidget(self)
        emptyLayout = QVBoxLayout(self.emptyWidget)
        emptyLayout.setSpacing(12)
        
        import os

        from PySide6.QtGui import QPixmap
        from qfluentwidgets import SubtitleLabel
        
        self.emptyImage = QLabel(self)
        icon_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "resource", "icon.png"))
        pixmap = QPixmap(icon_path)
        if not pixmap.isNull():
            pixmap = pixmap.scaled(100, 100, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
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
        
    def _update_empty_state(self):
        has_tasks = len(self.task_cards)
        self.commandBar.setVisible(has_tasks)
        self.scrollArea.setVisible(has_tasks)
        self.emptyWidget.setVisible(not has_tasks)
        
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
        self._update_empty_state()
        
    def _on_task_progress(self, task_id, downloaded, total):
        card = self.task_cards.get(task_id)
        if card:
            card.update_progress(downloaded, total)
            
    def _on_task_status_changed(self, task_id, new_status: TaskStatus):
        card = self.task_cards.get(task_id)
        if card:
            if new_status == TaskStatus.CANCELED:
                del self.task_cards[task_id]
                self.listLayout.removeWidget(card)
                card.deleteLater()
                self._update_empty_state()
            else:
                card.set_status(new_status)
            
    def _on_task_error(self, task_id, err_msg):
        card = self.task_cards.get(task_id)
        if card:
            card.set_error(err_msg)

    def _show_context_menu(self, pos):
        selected_items = self.scrollWidget.get_selected_items()
        target_card = self.scrollWidget._get_item_at(pos)
        
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
            action_open.triggered.connect(lambda: selected_items[0]._on_open())
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
        
    def _bulk_resume(self, items):
        task_ids = [item.task.id for item in items if hasattr(item, 'task') and item.task.status in (TaskStatus.PAUSED, TaskStatus.FAILED, TaskStatus.PENDING)]
        if task_ids:
            downloader_manager.resume_tasks(task_ids)
        self.scrollWidget.clear_selection()
                
    def _bulk_pause(self, items):
        task_ids = [item.task.id for item in items if hasattr(item, 'task') and item.task.status in (TaskStatus.PENDING, TaskStatus.DOWNLOADING)]
        if task_ids:
            downloader_manager.pause_tasks(task_ids)
        self.scrollWidget.clear_selection()
                
    def _bulk_cancel(self, items):
        valid_items = [item for item in items if hasattr(item, 'task') and item.task.status != TaskStatus.CANCELED]
        if not valid_items:
            return
            
        if cfg.show_cancel_prompt:
            w = CancelPromptDialog(len(valid_items), self.window())
            if not w.exec():
                return
            if w.checkbox.isChecked():
                cfg.show_cancel_prompt = False
                cfg.save()
                
        task_ids = []
        for item in valid_items:
            task_ids.append(item.task.id)
            self.listLayout.removeWidget(item)
            item.deleteLater()
            
        if task_ids:
            downloader_manager.cancel_tasks(task_ids)
        self.scrollWidget.clear_selection()

    def _start_all(self):
        task_ids = [card.task.id for card in self.task_cards.values() if card.task.status in (TaskStatus.PAUSED, TaskStatus.FAILED, TaskStatus.PENDING)]
        if task_ids:
            downloader_manager.resume_tasks(task_ids)
            InfoBar.success("🚀 批量启动", "已唤醒所有待命的任务，火力全开！", parent=self.window(), position=InfoBarPosition.TOP_RIGHT)

    def _pause_all(self):
        task_ids = [card.task.id for card in self.task_cards.values() if card.task.status in (TaskStatus.PENDING, TaskStatus.DOWNLOADING)]
        if task_ids:
            downloader_manager.pause_tasks(task_ids)
            InfoBar.warning("⏸️ 紧急刹车", "正在下载的任务已全部暂停", parent=self.window(), position=InfoBarPosition.TOP_RIGHT)

    def _clear_completed(self):
        to_remove = []
        for task_id, card in list(self.task_cards.items()):
            if card.task.status == TaskStatus.COMPLETED:
                to_remove.append(task_id)
                self.listLayout.removeWidget(card)
                card.deleteLater()
        if to_remove:
            downloader_manager.delete_tasks(to_remove, delete_files=False)
        for tid in to_remove:
            self.task_cards.pop(tid, None)
        if to_remove:
            self._update_empty_state()
            InfoBar.success("🧹 列表清理完成", "已抹除全部完成记录，保持界面清爽（您的文件完好无损）", parent=self.window(), position=InfoBarPosition.TOP_RIGHT)

    def _cancel_all(self):
        valid_items = [card for task_id, card in self.task_cards.items() if card.task.status != TaskStatus.CANCELED]
        if not valid_items:
            return
            
        if cfg.show_cancel_prompt:
            w = CancelPromptDialog(len(valid_items), self.window())
            if not w.exec():
                return
            if w.checkbox.isChecked():
                cfg.show_cancel_prompt = False
                cfg.save()
                
        to_remove = []
        for card in valid_items:
            to_remove.append(card.task.id)
            self.listLayout.removeWidget(card)
            card.deleteLater()
            
        if to_remove:
            downloader_manager.cancel_tasks(to_remove)
        self.task_cards.clear()
        self._update_empty_state()
