from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QCloseEvent, QAction
from PySide6.QtWidgets import QSystemTrayIcon, QMenu
from qfluentwidgets import FluentWindow, NavigationItemPosition, FluentIcon as FIF, InfoBadge, InfoBadgePosition
import os
from core.downloader import downloader_manager
from core.config import cfg

from ui.views.home_interface import HomeInterface
from ui.views.download_interface import DownloadInterface
from ui.views.setting_interface import NetworkSettingInterface, DownloadSettingInterface, AboutSettingInterface

class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.initWindow()
        
        # 初始化子页面
        self.homeInterface = HomeInterface(self)
        self.downloadInterface = DownloadInterface(self)
        self.networkSettingInterface = NetworkSettingInterface(self)
        self.downloadSettingInterface = DownloadSettingInterface(self)
        self.aboutSettingInterface = AboutSettingInterface(self)
        
        self.initNavigation()
        self._init_tray()
        downloader_manager.signals.speed_update.connect(self._update_speed_title)
        
    def _update_speed_title(self, speed_str):
        if speed_str:
            self.setWindowTitle(f'WNACG Downloader - {speed_str}')
        else:
            self.setWindowTitle('WNACG Downloader')
        
    def changeEvent(self, event):
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            for card in self.homeInterface.card_map.values():
                card.update_download_state()
        super().changeEvent(event)
        
    def initNavigation(self):
        self.addSubInterface(self.homeInterface, FIF.HOME, '漫画列表')
        self.addSubInterface(self.downloadInterface, FIF.DOWNLOAD, '任务列队')
        
        # 将设置页放置于导航栏底部
        self.addSubInterface(
            self.networkSettingInterface, FIF.GLOBE, '网络代理')
        self.addSubInterface(
            self.downloadSettingInterface, FIF.SETTING, '下载设置')
        self.addSubInterface(
            self.aboutSettingInterface, FIF.INFO, '系统关于')
            
        try:
            self.navigationInterface.setExpandWidth(220)
            from qfluentwidgets import setFont
            setFont(self.navigationInterface, 11)
        except Exception:
            pass
            
        item = self.navigationInterface.widget(self.downloadInterface.objectName())
        if item:
            self.downloadBadge = None
            
        downloader_manager.signals.badge_update.connect(self._update_badge)
        
    def _update_badge(self, count):
        item = self.navigationInterface.widget(self.downloadInterface.objectName())
        if not item: return
        
        if not hasattr(self, 'downloadBadge') or not self.downloadBadge:
            self.downloadBadge = InfoBadge.error(
                count, 
                parent=item.parent(), 
                target=item, 
                position=InfoBadgePosition.NAVIGATION_ITEM
            )
            
        if count > 0:
            self.downloadBadge.setNum(count)
            self.downloadBadge.show()
        else:
            self.downloadBadge.hide()
            
    def initWindow(self):
        self.resize(1060, 960)
        self.setMinimumWidth(600)
        self.setMinimumHeight(800)
        self.setWindowTitle('WNACG Downloader')
        
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "resource", "icon.png")
        self.setWindowIcon(QIcon(icon_path))
        
        desktop = self.screen().availableGeometry()
        w, h = desktop.width(), desktop.height()
        self.move(w//2 - self.width()//2, h//2 - self.height()//2)

    def _init_tray(self):
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "resource", "icon.png")
        self.trayIcon = QSystemTrayIcon(self)
        self.trayIcon.setIcon(QIcon(icon_path))
        
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

        from core.models import TaskStatus
        downloader_manager.signals.task_status_changed.connect(self._on_task_status_for_tray)
        
    def _on_task_status_for_tray(self, task_id, status):
        from core.models import TaskStatus
        if status == TaskStatus.COMPLETED and self.isHidden():
            import core.db as db
            task = db.get_task(task_id)
            if task:
                self.trayIcon.showMessage(
                    "下载完成", 
                    f"《{task.comic.title}》已下载完毕",
                    QSystemTrayIcon.MessageIcon.Information, 
                    3000
                )

    def _show_window(self):
        self.showNormal()
        self.activateWindow()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.isHidden():
                self._show_window()
            else:
                self.hide()
                
    def _force_quit(self):
        self.trayIcon.hide()
        from PySide6.QtWidgets import QApplication
        QApplication.quit()
        
    def closeEvent(self, e: QCloseEvent):
        if cfg.close_to_tray:
            e.ignore()
            self.hide()
            self.trayIcon.showMessage(
                "已最小化到托盘",
                "WNACG Downloader 将在后台继续运行",
                QSystemTrayIcon.MessageIcon.Information,
                2000
            )
        else:
            super().closeEvent(e)
