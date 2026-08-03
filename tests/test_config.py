import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from wnacg.infrastructure import config


def test_invalid_field_does_not_reset_valid_fields(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"max_concurrent_tasks": 7, "domain": "https://invalid/path"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CONFIG_FILE", config_path)

    loaded = config.load_config()

    assert loaded.max_concurrent_tasks == 7
    assert loaded.domain == "www.wnacg.com"


def test_corrupt_config_is_backed_up(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_FILE", config_path)

    loaded = config.load_config()

    assert loaded.domain == "www.wnacg.com"
    assert list(tmp_path.glob("config.invalid-*.json"))


def test_empty_download_directory_and_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        config.AppConfig(download_dir="   ")
    with pytest.raises(ValidationError):
        config.AppConfig(unknown_setting=True)  # pyright: ignore[reportCallIssue]


def test_config_save_is_atomic_and_leaves_no_temporary_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_FILE", config_path)
    settings = config.AppConfig(max_concurrent_tasks=4)

    settings.save()

    assert config.AppConfig.model_validate_json(config_path.read_text(encoding="utf-8")).max_concurrent_tasks == 4
    assert list(tmp_path.glob("*.tmp")) == []


def test_environment_overrides_file_without_becoming_persistent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"max_concurrent_tasks": 3}), encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_FILE", config_path)
    monkeypatch.setenv("WNACG_MAX_CONCURRENT_TASKS", "9")

    loaded = config.load_config()
    loaded.show_close_prompt = False
    loaded.save()

    assert loaded.max_concurrent_tasks == 9
    persisted = config.AppConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
    assert persisted.max_concurrent_tasks == 3
    assert persisted.show_close_prompt is False
