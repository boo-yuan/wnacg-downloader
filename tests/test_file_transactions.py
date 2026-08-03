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
    Image.new("RGB", (8, 8), "red").save(source)

    result = process_image(
        source,
        output,
        0,
        "https://img.example/same.png",
        DownloadOptions(image_format=DownloadFormat.JPG),
        20_000_000,
    )
    assert result.name == "0001-same.jpg"
    assert is_valid_image(result, 20_000_000)

    unrelated = output / "notes.txt"
    unrelated.write_text("preserve me", encoding="utf-8")
    reconcile_artifacts(
        task_id="task-1",
        source_directory=output,
        current_files=[result],
        pack_to_zip=True,
        delete_originals=True,
    )
    final_archive = archive_path(output)
    assert output.exists()
    assert unrelated.exists()
    assert not result.exists()
    assert final_archive.exists()
    assert not list(tmp_path.glob("*.tmp"))
    with zipfile.ZipFile(final_archive) as archive:
        assert archive.testzip() is None
        assert archive.namelist() == ["0001-same.jpg"]


def test_reconcile_removes_only_stale_manifest_files(tmp_path: Path) -> None:
    output = tmp_path / "Gallery [1]"
    output.mkdir()
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
    )

    second = output / "0001.webp"
    second.write_bytes(b"second")
    reconcile_artifacts(
        task_id="task-1",
        source_directory=output,
        current_files=[second],
        pack_to_zip=False,
        delete_originals=False,
    )

    assert not first.exists()
    assert second.exists()
    assert unrelated.exists()
