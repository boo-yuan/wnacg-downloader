"""Application entry point for WNACG Downloader."""

import sys
import time
from collections.abc import Callable


def main() -> int:
    """Start the Qt application, or validate UI construction in smoke-test mode."""
    smoke_test_requested = "--smoke-test" in sys.argv
    from wnacg.infrastructure.paths import initialize_paths

    initialize_paths()
    from PySide6.QtCore import QLockFile

    from wnacg.infrastructure.paths import DATA_DIR

    instance_lock = QLockFile(str(DATA_DIR / "application.lock"))
    instance_lock.setStaleLockTime(0)
    if not instance_lock.tryLock(100):
        return 2

    complete_logging_callback: Callable[[], None] | None = None
    try:
        from wnacg.infrastructure.logger import complete_logging, configure_logging

        configure_logging()
        complete_logging_callback = complete_logging
        from wnacg.infrastructure.config import cfg, initialize_config

        initialize_config()
        from wnacg.infrastructure.database import SQLiteTaskRepository, initialize_database

        initialize_database()
        if smoke_test_requested:
            cfg.download_dir = str(DATA_DIR / "downloads")

        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
        from qfluentwidgets import Theme, setTheme

        from wnacg.application.downloader import DownloaderWorker
        from wnacg.ui.components.cover_manager import CoverManagerClass
        from wnacg.ui.main_window import MainWindow

        task_repository = SQLiteTaskRepository()
        cover_manager = CoverManagerClass()
        downloader_manager = DownloaderWorker(task_repository)
        downloader_manager.prepare()

        QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
        application_arguments = [argument for argument in sys.argv if argument != "--smoke-test"]
        application = QApplication(application_arguments)
        setTheme(Theme.AUTO)

        window = MainWindow(downloader_manager, task_repository, cover_manager)

        def stop_services() -> None:
            shutdown_deadline = time.monotonic() + 20.0
            downloader_manager.stop(shutdown_deadline)
            cover_manager.stop(shutdown_deadline)
            window.stop_workers(shutdown_deadline)

        if smoke_test_requested:
            window.trayIcon.hide()
            window.deleteLater()
            application.processEvents()
            stop_services()
            return 0

        window.show()

        application.aboutToQuit.connect(stop_services)
        downloader_manager.start()

        return application.exec()
    finally:
        instance_lock.unlock()
        if complete_logging_callback is not None:
            complete_logging_callback()


if __name__ == "__main__":
    raise SystemExit(main())
