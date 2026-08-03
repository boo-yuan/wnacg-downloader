from pathlib import Path

from wnacg.infrastructure import paths


def test_legacy_copy_is_non_destructive_and_marked_complete(tmp_path: Path) -> None:
    source = tmp_path / "legacy"
    destination = tmp_path / "current"
    source.mkdir()
    destination.mkdir()
    (source / "old.txt").write_text("legacy", encoding="utf-8")
    (source / "conflict.txt").write_text("old", encoding="utf-8")
    (destination / "conflict.txt").write_text("new", encoding="utf-8")
    warnings: list[str] = []

    paths._copy_missing(source, destination, warnings)

    assert (destination / "old.txt").read_text(encoding="utf-8") == "legacy"
    assert (destination / "conflict.txt").read_text(encoding="utf-8") == "new"
    assert warnings


def test_initialize_data_dir_creates_marker(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    assert paths.initialize_data_dir(data_dir) == ()
    assert (data_dir / ".legacy-migration-v1.complete").is_file()
    assert paths.initialize_data_dir(data_dir) == ()
