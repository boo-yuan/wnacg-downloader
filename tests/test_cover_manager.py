"""Regression tests for bounded cover-worker lifecycle behavior."""

import gc
import os
import threading
import time
import weakref
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject
from PySide6.QtGui import QImage
from shiboken6 import isValid

from wnacg.ui.components import cover_manager
from wnacg.ui.components.cover_manager import CoverFetchTask, CoverManagerSignals, _CoverCallbackRef


def test_invalid_cover_url_always_finishes(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject_url(_url: str) -> str:
        raise ValueError("unsafe cover URL")

    received: list[tuple[str, bool]] = []

    def receive(url: str, image: QImage) -> None:
        received.append((url, image.isNull()))

    monkeypatch.setattr(cover_manager, "ensure_public_https_url_sync", reject_url)
    signals = CoverManagerSignals()
    signals.finished.connect(receive)

    CoverFetchTask("https://invalid.example/cover.jpg", signals, threading.Event()).run()

    assert received == [("https://invalid.example/cover.jpg", True)]


def test_cover_cache_enforces_total_size(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cache_directory = tmp_path / "covers"
    cache_directory.mkdir()
    oldest = cache_directory / "oldest.jpg"
    newest = cache_directory / "newest.jpg"
    oldest.write_bytes(b"123456")
    newest.write_bytes(b"abcdef")
    now = time.time()
    os.utime(oldest, (now - 10, now - 10))
    os.utime(newest, (now, now))
    monkeypatch.setattr(cover_manager, "_COVER_CACHE_DIR", cache_directory)
    monkeypatch.setattr(cover_manager, "_COVER_CACHE_MAX_BYTES", 6)

    cover_manager._expire_cover_cache()

    assert not oldest.exists()
    assert newest.exists()


class _CoverTarget(QObject):
    def receive(self, _url: str, _image: QImage) -> None:
        raise AssertionError("A deleted cover target must not be called")


def test_bound_cover_callback_does_not_keep_target_alive() -> None:
    target = _CoverTarget()
    target_weakref = weakref.ref(target)
    callback = _CoverCallbackRef(target.receive)

    del target
    gc.collect()

    assert target_weakref() is None
    assert not callback.invoke("https://example.com/cover.jpg", QImage())


def test_cover_callback_skips_deleted_cpp_object() -> None:
    application = QCoreApplication.instance()
    if application is None:
        application = QCoreApplication([])
    target = _CoverTarget()
    callback = _CoverCallbackRef(target.receive)

    target.deleteLater()
    QCoreApplication.sendPostedEvents(target, QEvent.Type.DeferredDelete)

    assert not isValid(target)
    assert not callback.invoke("https://example.com/cover.jpg", QImage())
