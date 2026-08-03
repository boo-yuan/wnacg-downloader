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

from wnacg.infrastructure.config import ProxyMode, cfg
from wnacg.infrastructure.crawler import WnacgCrawler
from wnacg.infrastructure.logger import logger
from wnacg.infrastructure.network_safety import (
    ensure_expected_content_type,
    ensure_public_https_url_sync,
    ensure_public_peer_address,
    read_limited_chunks,
)
from wnacg.infrastructure.paths import CACHE_DIR

_COVER_CACHE_DIR = CACHE_DIR / "covers"
_COVER_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
_COVER_CACHE_MAX_BYTES = 256 * 1024 * 1024
_COVER_CACHE_MAX_FILES = 2_000
_COVER_CACHE_LOCK = threading.RLock()
_IMAGE_CONTENT_TYPES = {"image/avif", "image/gif", "image/jpeg", "image/png", "image/webp"}
type CoverCallback = Callable[[str, QImage], None]


def get_temp_dir() -> str:
    """Return the application-owned cover cache directory."""
    _COVER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return str(_COVER_CACHE_DIR)


def _expire_cover_cache_unlocked() -> None:
    if not _COVER_CACHE_DIR.is_dir():
        return
    cutoff = time.time() - _COVER_CACHE_TTL_SECONDS
    retained_files: list[tuple[Path, float, int]] = []
    for cache_file in _COVER_CACHE_DIR.iterdir():
        try:
            if not cache_file.is_file() or cache_file.is_symlink():
                continue
            metadata = cache_file.stat()
            if metadata.st_mtime < cutoff:
                cache_file.unlink()
            else:
                retained_files.append((cache_file, metadata.st_mtime, metadata.st_size))
        except OSError as error:
            logger.warning("Expired cover cache removal failed", path=str(cache_file), error=str(error))

    retained_files.sort(key=lambda item: item[1])
    retained_bytes = sum(item[2] for item in retained_files)
    while retained_files and (len(retained_files) > _COVER_CACHE_MAX_FILES or retained_bytes > _COVER_CACHE_MAX_BYTES):
        cache_file, _modified_time, file_size = retained_files.pop(0)
        try:
            cache_file.unlink()
            retained_bytes -= file_size
        except OSError as error:
            logger.warning("Cover cache quota cleanup failed", path=str(cache_file), error=str(error))


def _expire_cover_cache() -> None:
    """Remove expired files and enforce cache byte/count quotas."""
    with _COVER_CACHE_LOCK:
        _expire_cover_cache_unlocked()


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
        try:
            safe_url = ensure_public_https_url_sync(self.url)
            filename = hashlib.sha256(self.url.encode()).hexdigest() + ".jpg"
            filepath = Path(get_temp_dir()) / filename
        except Exception as error:
            logger.warning("Cover URL validation failed", url=self.url, error=str(error))
            self.signals.finished.emit(self.url, QImage())
            return

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
                    if cfg.proxy_mode is ProxyMode.DIRECT:
                        ensure_public_peer_address(resp.primary_ip)
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
                    with _COVER_CACHE_LOCK:
                        temporary_path.write_bytes(content)
                        temporary_path.replace(filepath)
                    _expire_cover_cache()
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

    def stop(self, deadline: float | None = None) -> None:
        """Stop cover work without exceeding a shared application deadline."""
        shutdown_deadline = deadline or (time.monotonic() + 6.0)
        self._stop_event.set()
        self.cover_pool.clear()
        remaining_milliseconds = max(0, int((shutdown_deadline - time.monotonic()) * 1_000))
        if not self.cover_pool.waitForDone(remaining_milliseconds):
            logger.warning("Cover worker pool did not stop within timeout")
        self._pending_callbacks.clear()
