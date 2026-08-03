from pathlib import Path

import pytest

from wnacg.infrastructure import logger as app_logger


def test_structured_logger_flushes_queued_sink(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    log_path = tmp_path / "app.jsonl"
    monkeypatch.setattr(app_logger, "LOG_PATH", log_path)
    monkeypatch.setattr(app_logger, "_configured", False)

    app_logger.configure_logging()
    app_logger.logger.info("test event", task_id="task-1")
    app_logger.complete_logging()

    content = log_path.read_text(encoding="utf-8")
    assert "test event" in content
    assert "task-1" in content
