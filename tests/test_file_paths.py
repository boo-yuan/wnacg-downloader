from pathlib import Path

import pytest

from wnacg.application.file_paths import (
    archive_path,
    completed_task_directory,
    image_base_name,
    incomplete_task_directory,
    safe_component,
    task_directory,
    validated_task_directory,
)


def test_task_directory_uses_safe_title_without_gallery_id(tmp_path: Path) -> None:
    result = task_directory(tmp_path, "A/B:*?")

    assert result.parent == tmp_path.resolve()
    assert result.name == "AB"
    assert "[" not in result.name


def test_task_directory_state_prefix_round_trip() -> None:
    completed = Path("downloads/Gallery")
    incomplete = incomplete_task_directory(completed)

    assert incomplete == Path("downloads/_Gallery")
    assert completed_task_directory(incomplete) == completed


def test_image_original_name_has_no_sequence_prefix() -> None:
    assert image_base_name(0, "https://a.test/path/original-name.jpg", "original", 1) == "original-name"
    assert image_base_name(1, "https://b.test/other/second.png", "original", 1) == "second"


def test_legacy_prefixed_name_remains_derivable_for_resume() -> None:
    assert image_base_name(0, "https://a.test/path/same.jpg", "original", 2) == "0001-same"


def test_archive_path_preserves_dots() -> None:
    source = Path("root/title.v2 [42]")
    assert archive_path(source) == Path("root/title.v2 [42].zip")


def test_destructive_path_must_be_direct_child(tmp_path: Path) -> None:
    root = tmp_path / "downloads"
    valid = root / "comic [1]"
    assert validated_task_directory(valid, root) == valid.resolve()
    with pytest.raises(ValueError):
        validated_task_directory(root, root)
    with pytest.raises(ValueError):
        validated_task_directory(root / "nested" / "comic", root)


def test_task_directory_rejects_filesystem_links(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    root = tmp_path / "downloads"
    root.mkdir()
    linked = root / "linked"
    linked.mkdir()

    def is_test_junction(path: Path) -> bool:
        return path == linked

    monkeypatch.setattr(Path, "is_junction", is_test_junction)

    with pytest.raises(ValueError, match="filesystem link"):
        validated_task_directory(linked, root)


@pytest.mark.parametrize("value", ["CON", "..", 'a<b>c:"'])
def test_safe_component_rejects_problematic_names(value: str) -> None:
    result = safe_component(value, "fallback")
    assert result not in {"", ".", "..", "CON"}
