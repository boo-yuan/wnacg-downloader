"""Application entry point for WNACG Downloader."""

import sys


def main() -> int:
    """Start the Qt application, or validate UI construction in smoke-test mode."""
    smoke_test_requested = "--smoke-test" in sys.argv
    from wnacg.infrastructure.paths import initialize_paths

    initialize_paths()
    from wnacg.infrastructure.logger import complete_logging, configure_logging

    configure_logging()
    from wnacg.infrastructure.config import cfg, initialize_config

    initialize_config()
    from wnacg.infrastructure.database import initialize_database

    initialize_database()
    if smoke_test_requested:
        from wnacg.infrastructure.paths import DATA_DIR

        cfg.download_dir = str(DATA_DIR / "downloads")

    from PySide6.QtCore import QLockFile, Qt
    from PySide6.QtWidgets import QApplication
    from qfluentwidgets import Theme, setTheme

    from wnacg.application.downloader import DownloaderWorker
    from wnacg.infrastructure.database import task_repository
    from wnacg.ui.components.cover_manager import cover_manager
    from wnacg.ui.main_window import MainWindow

    downloader_manager = DownloaderWorker(task_repository)
    downloader_manager.prepare()

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    application_arguments = [argument for argument in sys.argv if argument != "--smoke-test"]
    application = QApplication(application_arguments)
    setTheme(Theme.AUTO)

    from wnacg.infrastructure.paths import DATA_DIR

    instance_lock = QLockFile(str(DATA_DIR / "application.lock"))
    instance_lock.setStaleLockTime(0)
    if not instance_lock.tryLock(100):
        return 2

    try:
        window = MainWindow(downloader_manager, task_repository)
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
        application.aboutToQuit.connect(window.stop_workers)
        downloader_manager.start()

        return application.exec()
    finally:
        instance_lock.unlock()
        complete_logging()


if __name__ == "__main__":
    raise SystemExit(main())
