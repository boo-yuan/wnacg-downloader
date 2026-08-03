import zipfile
from pathlib import Path

from PIL import Image

from wnacg.application.downloader import DownloaderWorker, is_valid_image
from wnacg.application.file_paths import archive_path
from wnacg.domain.models import DownloadFormat, DownloadOptions


def test_image_processing_and_zip_are_valid_and_atomic(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    output = tmp_path / "Gallery [1]"
    output.mkdir()
    Image.new("RGB", (8, 8), "red").save(source)

    result = DownloaderWorker._process_image(
        source,
        output,
        0,
        "https://img.example/same.png",
        DownloadOptions(image_format=DownloadFormat.JPG),
    )
    assert result.name == "0001-same.jpg"
    assert is_valid_image(result)

    DownloaderWorker._create_archive(output, delete_original=True)
    final_archive = archive_path(output)
    assert not output.exists()
    assert final_archive.exists()
    assert not list(tmp_path.glob("*.tmp"))
    with zipfile.ZipFile(final_archive) as archive:
        assert archive.testzip() is None
