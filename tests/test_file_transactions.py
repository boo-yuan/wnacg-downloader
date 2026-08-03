import zipfile
from pathlib import Path

from PIL import Image

from wnacg.application.artifacts import reconcile_artifacts
from wnacg.application.file_paths import archive_path
from wnacg.application.image_files import is_valid_image, process_image
from wnacg.domain.models import DownloadFormat, DownloadOptions


def test_image_processing_and_zip_are_valid_and_atomic(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    output = tmp_path / "Gallery [1]"
    output.mkdir()
    metadata = tmp_path / "metadata"
    Image.new("RGB", (8, 8), "red").save(source)

    result = process_image(
        source,
        output,
        0,
        "https://img.example/same.png",
        DownloadOptions(image_format=DownloadFormat.JPG),
        20_000_000,
    )
    assert result.name == "same.jpg"
    assert is_valid_image(result, 20_000_000)

    unrelated = output / "notes.txt"
    unrelated.write_text("preserve me", encoding="utf-8")
    reconcile_artifacts(
        task_id="task-1",
        source_directory=output,
        current_files=[result],
        pack_to_zip=True,
        delete_originals=True,
        metadata_directory=metadata,
    )
    final_archive = archive_path(output)
    assert output.exists()
    assert unrelated.exists()
    assert not result.exists()
    assert final_archive.exists()
    assert not list(tmp_path.glob("*.tmp"))
    with zipfile.ZipFile(final_archive) as archive:
        assert archive.testzip() is None
        assert archive.namelist() == ["same.jpg"]
    assert not (output / ".wnacg-manifest.json").exists()
    assert len(list(metadata.glob("*.json"))) == 1


def test_reconcile_removes_only_stale_manifest_files(tmp_path: Path) -> None:
    output = tmp_path / "Gallery [1]"
    output.mkdir()
    metadata = tmp_path / "metadata"
    first = output / "0001.jpg"
    first.write_bytes(b"first")
    unrelated = output / "user-notes.txt"
    unrelated.write_text("keep", encoding="utf-8")
    reconcile_artifacts(
        task_id="task-1",
        source_directory=output,
        current_files=[first],
        pack_to_zip=False,
        delete_originals=False,
        metadata_directory=metadata,
    )

    second = output / "0001.webp"
    second.write_bytes(b"second")
    reconcile_artifacts(
        task_id="task-1",
        source_directory=output,
        current_files=[second],
        pack_to_zip=False,
        delete_originals=False,
        metadata_directory=metadata,
    )

    assert not first.exists()
    assert second.exists()
    assert unrelated.exists()


def test_redownload_without_packing_removes_stale_archive(tmp_path: Path) -> None:
    output = tmp_path / "Gallery [2]"
    output.mkdir()
    metadata = tmp_path / "metadata"
    first = output / "0001.jpg"
    first.write_bytes(b"first")
    reconcile_artifacts(
        task_id="task-2",
        source_directory=output,
        current_files=[first],
        pack_to_zip=True,
        delete_originals=True,
        metadata_directory=metadata,
    )
    final_archive = archive_path(output)
    assert final_archive.exists()
    assert not (output / ".wnacg-manifest.json").exists()
    assert len(list(metadata.glob("*.json"))) == 1

    output.mkdir()
    replacement = output / "0001.jpg"
    replacement.write_bytes(b"replacement")
    reconcile_artifacts(
        task_id="task-2",
        source_directory=output,
        current_files=[replacement],
        pack_to_zip=False,
        delete_originals=False,
        metadata_directory=metadata,
    )

    assert not final_archive.exists()
    assert replacement.exists()


def test_legacy_in_folder_manifest_is_migrated(tmp_path: Path) -> None:
    output = tmp_path / "Gallery"
    output.mkdir()
    metadata = tmp_path / "metadata"
    legacy = output / ".wnacg-manifest.json"
    legacy.write_text(
        '{"version":1,"task_id":"task-legacy","files":["one.jpg"],"archive_created":false}',
        encoding="utf-8",
    )
    current = output / "one.jpg"
    current.write_bytes(b"image")

    reconcile_artifacts(
        task_id="task-legacy",
        source_directory=output,
        current_files=[current],
        pack_to_zip=False,
        delete_originals=False,
        metadata_directory=metadata,
    )

    assert not legacy.exists()
    assert len(list(metadata.glob("*.json"))) == 1
