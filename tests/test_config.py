import json
from pathlib import Path

from wnacg.infrastructure import config


def test_invalid_field_does_not_reset_valid_fields(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"max_concurrent_tasks": 7, "domain": "https://invalid/path"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "CONFIG_FILE", config_path)

    loaded = config.load_config()

    assert loaded.max_concurrent_tasks == 7
    assert loaded.domain == "www.wnacg.com"


def test_corrupt_config_is_backed_up(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text("{not-json", encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_FILE", config_path)

    loaded = config.load_config()

    assert loaded.domain == "www.wnacg.com"
    assert list(tmp_path.glob("config.invalid-*.json"))
