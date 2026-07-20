from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from qfluentwidgets import FluentWindow, NavigationItemPosition, FluentIcon as FIF, InfoBadge, InfoBadgePosition
import os
from core.downloader import downloader_manager

from ui.views.home_interface import HomeInterface
from ui.views.download_interface import DownloadInterface
from ui.views.setting_interface import SettingInterface

class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.initWindow()
        
        # 初始化子页面
        self.homeInterface = HomeInterface(self)
        self.downloadInterface = DownloadInterface(self)
        self.settingInterface = SettingInterface(self)
        
        self.initNavigation()
        
    def initNavigation(self):
        self.addSubInterface(self.homeInterface, FIF.HOME, '首页')
        self.addSubInterface(self.downloadInterface, FIF.DOWNLOAD, '下载管理')
        
        # 将设置页放置于导航栏底部
        self.addSubInterface(
            self.settingInterface, FIF.SETTING, '设置', NavigationItemPosition.BOTTOM)
            
        item = self.navigationInterface.widget(self.downloadInterface.objectName())
        if item:
            self.downloadBadge = None
            
        downloader_manager.signals.badge_update.connect(self._update_badge)
        
    def _update_badge(self, count):
        item = self.navigationInterface.widget(self.downloadInterface.objectName())
        if not item: return
        
        if count > 0:
            if not hasattr(self, 'downloadBadge') or not self.downloadBadge:
                self.downloadBadge = InfoBadge.error(
                    count, 
                    parent=item.parent(), 
                    target=item, 
                    position=InfoBadgePosition.NAVIGATION_ITEM
                )
            else:
                self.downloadBadge.setNum(count)
                self.downloadBadge.show()
        else:
            if hasattr(self, 'downloadBadge') and self.downloadBadge:
                self.downloadBadge.deleteLater()
                self.downloadBadge = None
            
    def initWindow(self):
        self.resize(1060, 960)
        self.setMinimumWidth(600)
        self.setMinimumHeight(800)
        self.setWindowTitle('WNACG Downloader')
        
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "resource", "icon.png")
        self.setWindowIcon(QIcon(icon_path))
        
        # 窗口居中
        desktop = self.screen().availableGeometry()
        w, h = desktop.width(), desktop.height()
        self.move(w//2 - self.width()//2, h//2 - self.height()//2)
