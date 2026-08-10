"""Main Fluent window, tray integration, and close behavior."""
# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false

import contextlib
import time
from pathlib import Path

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QAction, QCloseEvent, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget
from qfluentwidgets import (
    BodyLabel,
    CheckBox,
    FluentWindow,
    InfoBadge,
    InfoBadgePosition,
    MessageBoxBase,
    SubtitleLabel,
    qconfig,
)
from qfluentwidgets import FluentIcon as FIF

from wnacg.application.downloader import DownloaderWorker
from wnacg.application.ports import TaskRepository
from wnacg.domain.models import TaskStatus
from wnacg.infrastructure.config import cfg
from wnacg.infrastructure.logger import logger
from wnacg.ui.components.cover_manager import CoverManagerClass
from wnacg.ui.theme import danger_text_style
from wnacg.ui.views.download_interface import DownloadInterface
from wnacg.ui.views.home_interface import HomeInterface
from wnacg.ui.views.setting_interface import (
    AboutSettingInterface,
    DownloadSettingInterface,
    NetworkSettingInterface,
)

DEFAULT_WINDOW_WIDTH = 1200
DEFAULT_WINDOW_HEIGHT = 720
MINIMUM_WINDOW_WIDTH = 960
MINIMUM_WINDOW_HEIGHT = 600
SCREEN_EDGE_MARGIN = 48


def calculate_window_sizes(available_width: int, available_height: int) -> tuple[tuple[int, int], tuple[int, int]]:
    """Return DPI-independent target and minimum sizes that fit the current screen."""
    usable_width = max(1, available_width - (SCREEN_EDGE_MARGIN * 2))
    usable_height = max(1, available_height - (SCREEN_EDGE_MARGIN * 2))
    target_size = (min(DEFAULT_WINDOW_WIDTH, usable_width), min(DEFAULT_WINDOW_HEIGHT, usable_height))
    minimum_size = (min(MINIMUM_WINDOW_WIDTH, usable_width), min(MINIMUM_WINDOW_HEIGHT, usable_height))
    return target_size, minimum_size


class ClosePromptDialog(MessageBoxBase):
    def __init__(self, active_count: int = 0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._has_active_downloads = active_count > 0
        self.titleLabel = SubtitleLabel("确认关闭", self)
        self.checkbox = CheckBox("记住本次选择，下次不再提示", self)

        if active_count > 0:
            text = (
                f"⚠️ 注意：还有 {active_count} 个任务正在下载！\n彻底退出会中断下载。\n\n"
                "建议您选择“最小化到托盘”，让它在后台默默下载。"
            )
            self.contentLabel = BodyLabel(text, self)
        else:
            self.contentLabel = BodyLabel("要彻底退出软件，还是隐藏到后台系统托盘？", self)

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.contentLabel)
        self.viewLayout.addWidget(self.checkbox)

        self.viewLayout.setSpacing(12)
        self.viewLayout.setContentsMargins(24, 24, 24, 24)

        self.yesButton.setText("最小化到托盘")
        self.cancelButton.setText("彻底退出")
        qconfig.themeChanged.connect(self._apply_theme_colors)
        self._apply_theme_colors()

    def _apply_theme_colors(self, _theme: object | None = None) -> None:
        style = danger_text_style(bold=True) if self._has_active_downloads else ""
        self.contentLabel.setStyleSheet(style)


class MainWindow(FluentWindow):
    def __init__(
        self,
        downloader: DownloaderWorker,
        repository: TaskRepository,
        cover_manager: CoverManagerClass,
    ) -> None:
        super().__init__()
        self._downloader = downloader
        self._repository = repository
        self.initWindow()

        # 初始化子页面
        self.homeInterface = HomeInterface(downloader, repository, cover_manager, self)
        self.downloadInterface = DownloadInterface(downloader, repository, self)
        self.networkSettingInterface = NetworkSettingInterface(downloader.apply_runtime_limits, self)
        self.downloadSettingInterface = DownloadSettingInterface(self)
        self.aboutSettingInterface = AboutSettingInterface(self)

        self.initNavigation()
        self._init_tray()
        downloader.signals.speed_update.connect(self._update_speed_title, Qt.ConnectionType.QueuedConnection)

    def _update_speed_title(self, speed_str: str) -> None:
        if speed_str:
            self.setWindowTitle(f"WNACG Downloader - {speed_str}")
        else:
            self.setWindowTitle("WNACG Downloader")

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self.homeInterface.refresh_card_states(force=True)
        super().changeEvent(event)

    def initNavigation(self) -> None:
        self.addSubInterface(self.homeInterface, FIF.HOME, "漫画列表")
        self.addSubInterface(self.downloadInterface, FIF.DOWNLOAD, "任务列队")

        # 将设置页放置于导航栏底部
        self.addSubInterface(self.networkSettingInterface, FIF.GLOBE, "网络代理")
        self.addSubInterface(self.downloadSettingInterface, FIF.SETTING, "下载设置")
        self.addSubInterface(self.aboutSettingInterface, FIF.INFO, "系统关于")

        try:
            self.navigationInterface.setExpandWidth(220)
            from qfluentwidgets import setFont

            setFont(self.navigationInterface, 11)
        except Exception as error:
            logger.warning("Navigation styling failed", error=str(error))

        item = self.navigationInterface.widget(self.downloadInterface.objectName())
        if item:
            self.downloadBadge = None

        self._downloader.signals.badge_update.connect(self._update_badge, Qt.ConnectionType.QueuedConnection)

    def _update_badge(self, count: int) -> None:
        item = self.navigationInterface.widget(self.downloadInterface.objectName())
        if not item:
            return

        if count > 0:
            if not hasattr(self, "downloadBadge") or not self.downloadBadge:
                self.downloadBadge = InfoBadge.error(
                    count, parent=item.parent(), target=item, position=InfoBadgePosition.NAVIGATION_ITEM
                )
            self.downloadBadge.setText(str(count))
            self.downloadBadge.adjustSize()
            self.downloadBadge.show()
        else:
            if hasattr(self, "downloadBadge") and self.downloadBadge:
                self.downloadBadge.deleteLater()
                self.downloadBadge = None

        if not hasattr(self, "_previous_count"):
            self._previous_count = 0

        self._previous_count = count

    def initWindow(self) -> None:
        self.setWindowTitle("WNACG Downloader")

        icon_path = Path(__file__).resolve().parents[1] / "resource" / "icon.png"
        self.setWindowIcon(QIcon(str(icon_path)))

        desktop = self.screen().availableGeometry()
        target_size, minimum_size = calculate_window_sizes(desktop.width(), desktop.height())
        self.setMinimumSize(*minimum_size)
        self.resize(*target_size)
        self.move(
            desktop.x() + (desktop.width() - self.width()) // 2,
            desktop.y() + (desktop.height() - self.height()) // 2,
        )

    def _init_tray(self) -> None:
        icon_path = Path(__file__).resolve().parents[1] / "resource" / "icon.png"
        self.trayIcon = QSystemTrayIcon(self)
        self.trayIcon.setIcon(QIcon(str(icon_path)))

        self.trayMenu = QMenu(self)
        self.showAction = QAction("显示主界面", self)
        self.showAction.triggered.connect(self._show_window)
        self.quitAction = QAction("彻底退出", self)
        self.quitAction.triggered.connect(self._force_quit)

        self.trayMenu.addAction(self.showAction)
        self.trayMenu.addSeparator()
        self.trayMenu.addAction(self.quitAction)

        self.trayIcon.setContextMenu(self.trayMenu)
        self.trayIcon.activated.connect(self._on_tray_activated)
        self.trayIcon.show()

        self._downloader.signals.task_status_changed.connect(
            self._on_task_status_for_tray,
            Qt.ConnectionType.QueuedConnection,
        )

    def _on_task_status_for_tray(self, task_id: str, status: TaskStatus) -> None:
        if status == TaskStatus.COMPLETED:
            active_count = self._repository.count_tasks(frozenset({TaskStatus.PENDING, TaskStatus.DOWNLOADING}))

            if active_count == 0:
                from qfluentwidgets import InfoBar, InfoBarPosition

                InfoBar.success(
                    title="🎉 下载大满贯！",
                    content="队列里的漫画全都下完啦，快去欣赏吧。",
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP_RIGHT,
                    duration=5000,
                    parent=self,
                )
                if self.isHidden() or self.isMinimized():
                    self.trayIcon.showMessage(
                        "🎉 任务全部完成",
                        "您挂机的下载队列已经全部搞定！",
                        QSystemTrayIcon.MessageIcon.Information,
                        3000,
                    )
            elif self.isHidden() or self.isMinimized():
                task = self._repository.get_task(task_id)
                if task:
                    self.trayIcon.showMessage(
                        "✅ 单本下载成功",
                        f"《{task.comic.title}》已经下好啦，躺在硬盘里等您翻阅。",
                        QSystemTrayIcon.MessageIcon.Information,
                        3000,
                    )

    def _show_window(self) -> None:
        self.showNormal()
        self.activateWindow()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.isHidden():
                self._show_window()
            else:
                self.hide()

    def _force_quit(self) -> None:
        self.trayIcon.hide()
        from PySide6.QtWidgets import QApplication

        QApplication.quit()

    def stop_workers(self, deadline: float | None = None) -> None:
        """Stop UI-owned background workers before application shutdown."""
        shutdown_deadline = deadline or (time.monotonic() + 16.0)
        self.homeInterface.stop_workers(shutdown_deadline)
        self.networkSettingInterface.stop_workers(shutdown_deadline)
        self.aboutSettingInterface.stop_workers(shutdown_deadline)

    def closeEvent(self, e: QCloseEvent) -> None:
        if cfg.show_close_prompt:
            from wnacg.domain.models import TaskStatus

            active_count = self._repository.count_tasks(frozenset({TaskStatus.PENDING, TaskStatus.DOWNLOADING}))

            w = ClosePromptDialog(active_count, self.window())
            if w.exec():
                if w.checkbox.isChecked():
                    cfg.show_close_prompt = False
                    cfg.close_to_tray = True
                    cfg.save()

                    # Update settings UI if we can
                    with contextlib.suppress(RuntimeError):
                        self.aboutSettingInterface.closeActionCard.comboBox.setCurrentIndex(1)

                e.ignore()
                self.hide()
                self.trayIcon.showMessage(
                    "已最小化到托盘", "WNACG Downloader 将在后台继续运行", QSystemTrayIcon.MessageIcon.Information, 2000
                )
            else:
                if w.checkbox.isChecked():
                    cfg.show_close_prompt = False
                    cfg.close_to_tray = False
                    cfg.save()

                    with contextlib.suppress(RuntimeError):
                        self.aboutSettingInterface.closeActionCard.comboBox.setCurrentIndex(2)

                self._force_quit()
        else:
            if cfg.close_to_tray:
                e.ignore()
                self.hide()
                self.trayIcon.showMessage(
                    "已最小化到托盘", "WNACG Downloader 将在后台继续运行", QSystemTrayIcon.MessageIcon.Information, 2000
                )
            else:
                self._force_quit()
