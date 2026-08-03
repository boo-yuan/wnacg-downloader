import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from qfluentwidgets import Theme, setTheme

# 确保在导入模块之前将 src 加入 sys.path（或者在 run.bat 中设置了 PYTHONPATH?
from ui.main_window import MainWindow


def main():
    # 启用?DPI 缩放支持
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    
    app = QApplication(sys.argv)
    
    # 设置主题为自动跟随系?(Win 11 风格)
    setTheme(Theme.AUTO)
    
    window = MainWindow()
    window.show()
    
    from core.downloader import downloader_manager
    from ui.components.cover_manager import cover_manager
    app.aboutToQuit.connect(downloader_manager.stop)
    app.aboutToQuit.connect(cover_manager.stop)
    
    # 启动后台下载引擎
    downloader_manager.start()
    
    sys.exit(app.exec())

if __name__ == '__main__':
    main()

import os, sys
