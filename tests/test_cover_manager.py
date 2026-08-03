"""Regression tests for bounded cover-worker lifecycle behavior."""

import os
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtGui import QImage

from wnacg.ui.components import cover_manager
from wnacg.ui.components.cover_manager import CoverFetchTask, CoverManagerSignals


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
