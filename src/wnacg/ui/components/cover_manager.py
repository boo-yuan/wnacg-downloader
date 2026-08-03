"""Bounded cover-image cache and Qt worker-pool adapter."""

import atexit
import contextlib
import hashlib
import os
import shutil
from pathlib import Path

from curl_cffi import requests
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QImage

from wnacg.infrastructure.config import cfg
from wnacg.infrastructure.logger import logger

_temp_dirs = set()


def get_temp_dir() -> str:
    path = str(Path(cfg.download_dir) / ".temp_covers")
    if path not in _temp_dirs:
        os.makedirs(path, exist_ok=True)
        _temp_dirs.add(path)
    return path


def _cleanup_covers():
    for d in _temp_dirs:
        with contextlib.suppress(Exception):
            shutil.rmtree(d, ignore_errors=True)
    with contextlib.suppress(Exception):
        shutil.rmtree("data/.temp_covers", ignore_errors=True)


# Register cleanup on exit
atexit.register(_cleanup_covers)


class CoverManagerSignals(QObject):
    finished = Signal(str, QImage)


class CoverFetchTask(QRunnable):
    def __init__(self, url, signals):
        super().__init__()
        self.url = url
        self.signals = signals

    def run(self):
        filename = hashlib.sha256(self.url.encode()).hexdigest() + ".jpg"
        filepath = os.path.join(get_temp_dir(), filename)

        # 1. Load from disk if exists
        if os.path.exists(filepath):
            img = QImage(filepath)
            if not img.isNull():
                self.signals.finished.emit(self.url, img)
                return
            else:
                with contextlib.suppress(Exception):
                    os.remove(filepath)

        # 2. Download and write to disk
        kwargs = {
            "impersonate": "chrome",
            "timeout": 5.0,
        }
        if cfg.proxy_mode == "custom":
            kwargs["proxies"] = cfg.curl_cffi_proxies
        elif cfg.proxy_mode == "direct":
            kwargs["trust_env"] = False
        else:
            kwargs["trust_env"] = True

        for attempt in range(3):
            try:
                with requests.Session(**kwargs) as s:
                    resp = s.get(self.url)

                resp.raise_for_status()
                if len(resp.content) > 10 * 1024 * 1024:
                    raise ValueError("Cover image exceeds 10 MiB limit")
                img = QImage()
                if img.loadFromData(resp.content):
                    temporary_path = Path(f"{filepath}.tmp")
                    temporary_path.write_bytes(resp.content)
                    temporary_path.replace(filepath)
                    self.signals.finished.emit(self.url, img)
                    return
                raise ValueError("Invalid image data")
            except Exception as e:
                if attempt == 2:
                    logger.warning(f"CoverFetchTask failed for {self.url} after 3 attempts: {e}")
                else:
                    import time

                    time.sleep(1.0)

        self.signals.finished.emit(self.url, QImage())


class CoverManagerClass(QObject):
    def __init__(self):
        super().__init__()

        self.pool = QThreadPool.globalInstance()
        # Limit to 5 concurrent cover downloads to prevent anti-bot blocking
        # QThreadPool.setMaxThreadCount applies to all tasks in the global instance
        # If maxThreadCount is lower than 5, we bump it to handle IO easily.
        # But wait, QThreadPool usually has lots of threads (e.g. 8-16 depending on CPU)
        # To limit only covers to 5, we should probably just use our own thread pool!
        self.cover_pool = QThreadPool()
        self.cover_pool.setMaxThreadCount(5)
        self.signals = CoverManagerSignals()
        self.signals.finished.connect(self._on_task_finished)

        # In-flight task tracker to avoid duplicate downloads of the same URL
        self._pending_callbacks = {}

    def load(self, url, callback=None):
        if not url:
            return
        # Disk cache loading is handled asynchronously inside CoverFetchTask.
        if url in self._pending_callbacks:
            if callback:
                self._pending_callbacks[url].append(callback)
            return

        self._pending_callbacks[url] = [callback] if callback else []
        task = CoverFetchTask(url, self.signals)
        self.cover_pool.start(task)

    def preload(self, url):
        self.load(url, None)

    def _on_task_finished(self, url, img):
        if url in self._pending_callbacks:
            cbs = self._pending_callbacks.pop(url)
            for cb in cbs:
                if cb:
                    try:
                        cb(url, img)
                    except RuntimeError:
                        # The UI component (like ComicCard) has been deleted
                        pass
                    except Exception as e:
                        logger.error(f"Callback error in CoverManager: {e}")

    def stop(self):
        self.cover_pool.clear()
        self.cover_pool.waitForDone(1000)
        self._pending_callbacks.clear()


# Global singleton
cover_manager = CoverManagerClass()
