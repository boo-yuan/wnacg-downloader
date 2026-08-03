"""Application entry point for WNACG Downloader."""

import sys


def main() -> int:
    """Start the Qt application, or validate UI construction in smoke-test mode."""
    smoke_test_requested = "--smoke-test" in sys.argv
    if smoke_test_requested:
        from wnacg.infrastructure.config import cfg
        from wnacg.infrastructure.database import initialize_database
        from wnacg.infrastructure.paths import DATA_DIR

        cfg.download_dir = str(DATA_DIR / "downloads")
        initialize_database()

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from qfluentwidgets import Theme, setTheme

    from wnacg.application.downloader import downloader_manager
    from wnacg.ui.components.cover_manager import cover_manager
    from wnacg.ui.main_window import MainWindow

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    application_arguments = [argument for argument in sys.argv if argument != "--smoke-test"]
    application = QApplication(application_arguments)
    setTheme(Theme.AUTO)

    window = MainWindow()
    if smoke_test_requested:
        window.trayIcon.hide()
        window.deleteLater()
        application.processEvents()
        cover_manager.stop()
        downloader_manager.stop()
        return 0

    window.show()

    application.aboutToQuit.connect(downloader_manager.stop)
    application.aboutToQuit.connect(cover_manager.stop)
    downloader_manager.start()

    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
