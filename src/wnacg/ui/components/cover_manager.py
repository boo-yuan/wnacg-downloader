"""Bounded cover-image cache and Qt worker-pool adapter."""

import contextlib
import hashlib
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import cast

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QImage

from wnacg.infrastructure.config import cfg
from wnacg.infrastructure.crawler import WnacgCrawler
from wnacg.infrastructure.logger import logger
from wnacg.infrastructure.network_safety import (
    ensure_expected_content_type,
    ensure_public_https_url_sync,
    read_limited_chunks,
)
from wnacg.infrastructure.paths import CACHE_DIR

_COVER_CACHE_DIR = CACHE_DIR / "covers"
_COVER_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
_IMAGE_CONTENT_TYPES = {"image/avif", "image/gif", "image/jpeg", "image/png", "image/webp"}
type CoverCallback = Callable[[str, QImage], None]


def get_temp_dir() -> str:
    """Return the application-owned cover cache directory."""
    _COVER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return str(_COVER_CACHE_DIR)


def _expire_cover_cache() -> None:
    """Remove only expired regular files from the application-owned cache."""
    if not _COVER_CACHE_DIR.is_dir():
        return
    cutoff = time.time() - _COVER_CACHE_TTL_SECONDS
    for cache_file in _COVER_CACHE_DIR.iterdir():
        try:
            if cache_file.is_file() and not cache_file.is_symlink() and cache_file.stat().st_mtime < cutoff:
                cache_file.unlink()
        except OSError as error:
            logger.warning("Expired cover cache removal failed", path=str(cache_file), error=str(error))


class CoverManagerSignals(QObject):
    finished = Signal(str, QImage)


class CoverFetchTask(QRunnable):
    def __init__(self, url: str, signals: "CoverManagerSignals", stop_event: threading.Event) -> None:
        super().__init__()
        self.url = url
        self.signals = signals
        self.stop_event = stop_event

    def run(self) -> None:
        if self.stop_event.is_set():
            return
        safe_url = ensure_public_https_url_sync(self.url)
        filename = hashlib.sha256(self.url.encode()).hexdigest() + ".jpg"
        filepath = Path(get_temp_dir()) / filename

        # 1. Load from disk if exists
        if filepath.exists():
            img = QImage(str(filepath))
            if not img.isNull():
                self.signals.finished.emit(self.url, img)
                return
            else:
                with contextlib.suppress(Exception):
                    filepath.unlink()

        # 2. Download and write to disk
        for attempt in range(3):
            try:
                with WnacgCrawler.get_sync_client() as client:
                    resp = client.get(safe_url, timeout=5.0, stream=True)
                    resp.raise_for_status()
                    ensure_public_https_url_sync(str(resp.url))
                    ensure_expected_content_type(resp.headers, _IMAGE_CONTENT_TYPES)
                    content_length = int(resp.headers.get("content-length", "0") or 0)
                    if content_length > cfg.max_cover_bytes:
                        raise ValueError(f"Cover image exceeds {cfg.max_cover_bytes} byte limit")
                    chunks = cast(
                        Iterable[bytes],
                        resp.iter_content(chunk_size=64 * 1024),  # pyright: ignore[reportUnknownMemberType]
                    )
                    content = read_limited_chunks(chunks, cfg.max_cover_bytes)
                if self.stop_event.is_set():
                    return
                img = QImage()
                if img.loadFromData(content) and img.width() * img.height() <= cfg.max_image_pixels:
                    temporary_path = filepath.with_name(f".{filename}.{uuid.uuid4().hex}.tmp")
                    temporary_path.write_bytes(content)
                    temporary_path.replace(filepath)
                    self.signals.finished.emit(self.url, img)
                    return
                raise ValueError("Invalid image data")
            except Exception as e:
                if attempt == 2:
                    logger.warning(f"CoverFetchTask failed for {self.url} after 3 attempts: {e}")
                else:
                    self.stop_event.wait(1.0)

        self.signals.finished.emit(self.url, QImage())


class CoverManagerClass(QObject):
    def __init__(self) -> None:
        super().__init__()

        _expire_cover_cache()
        self.cover_pool = QThreadPool()
        self.cover_pool.setMaxThreadCount(5)
        self.signals = CoverManagerSignals()
        self.signals.finished.connect(self._on_task_finished)

        # In-flight task tracker to avoid duplicate downloads of the same URL
        self._pending_callbacks: dict[str, list[CoverCallback]] = {}
        self._stop_event = threading.Event()

    def load(self, url: str, callback: CoverCallback | None = None) -> None:
        if not url or self._stop_event.is_set():
            return
        # Disk cache loading is handled asynchronously inside CoverFetchTask.
        if url in self._pending_callbacks:
            if callback:
                self._pending_callbacks[url].append(callback)
            return

        self._pending_callbacks[url] = [callback] if callback else []
        task = CoverFetchTask(url, self.signals, self._stop_event)
        self.cover_pool.start(task)

    def preload(self, url: str) -> None:
        self.load(url, None)

    def _on_task_finished(self, url: str, img: QImage) -> None:
        if url in self._pending_callbacks:
            cbs = self._pending_callbacks.pop(url)
            for cb in cbs:
                try:
                    cb(url, img)
                except RuntimeError:
                    logger.debug("Cover callback target was already deleted", url=url)
                except Exception as error:
                    logger.error("Cover callback failed", url=url, error=str(error))

    def stop(self) -> None:
        self._stop_event.set()
        self.cover_pool.clear()
        if not self.cover_pool.waitForDone(6_000):
            logger.warning("Cover worker pool did not stop within timeout")
        self._pending_callbacks.clear()


# Global singleton
cover_manager = CoverManagerClass()
