import sys
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from qfluentwidgets import setTheme, Theme

# 确保在导入模块之前将 src 加入 sys.path（或者在 run.bat 中设置了 PYTHONPATH）
from ui.main_window import MainWindow

def main():
    # 启用高 DPI 缩放支持
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    
    app = QApplication(sys.argv)
    
    # 设置主题为自动跟随系统 (Win 11 风格)
    setTheme(Theme.AUTO)
    
    window = MainWindow()
    window.show()
    
    # 启动后台下载引擎
    from core.downloader import downloader_manager
    downloader_manager.start()
    
    from ui.components.cover_manager import cover_manager
    app.aboutToQuit.connect(downloader_manager.stop)
    app.aboutToQuit.connect(cover_manager.stop)
    
    sys.exit(app.exec())

if __name__ == '__main__':
    main()
