"""Validated image naming, decoding, conversion, and output discovery."""

import shutil
import uuid
import warnings
from pathlib import Path

import PIL.Image

from wnacg.application.file_paths import image_base_name
from wnacg.application.ports import ImageRecord
from wnacg.domain.models import DownloadOptions, DownloadTask


def is_valid_image(file_path: Path, maximum_pixels: int) -> bool:
    """Validate image headers and decoded payload within the pixel budget."""
    if not file_path.exists() or file_path.stat().st_size == 0:
        return False
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", PIL.Image.DecompressionBombWarning)
            with PIL.Image.open(file_path) as image:
                if image.width * image.height > maximum_pixels:
                    return False
                image.verify()
            with PIL.Image.open(file_path) as image:
                image.load()
        return True
    except Exception:
        return False


def _output_extensions(options: DownloadOptions) -> tuple[str, ...]:
    return (
        tuple(sorted(PIL.Image.registered_extensions()))
        if options.image_format.value == "original"
        else (f".{options.image_format.value}",)
    )


def preferred_image_paths(task: DownloadTask, image: ImageRecord, options: DownloadOptions) -> list[Path]:
    """Return current-policy output paths for one persisted image record."""
    base_name = image_base_name(image["image_index"], image["raw_url"], options.naming.value, options.naming_version)
    return [Path(task.save_path) / f"{base_name}{extension}" for extension in _output_extensions(options)]


def expected_image_paths(task: DownloadTask, image: ImageRecord, options: DownloadOptions) -> list[Path]:
    """Return persisted, current, and legacy output candidates for safe resume."""
    candidates: list[Path] = []
    recorded_name = image["output_name"]
    if recorded_name and Path(recorded_name).name == recorded_name and recorded_name not in {".", ".."}:
        candidates.append(Path(task.save_path) / recorded_name)
    candidates.extend(preferred_image_paths(task, image, options))
    if options.naming.value == "original" and image["raw_url"]:
        legacy_version = 2 if options.naming_version == 1 else 1
        legacy_options = options.model_copy(update={"naming_version": legacy_version})
        candidates.extend(preferred_image_paths(task, image, legacy_options))
    return list(dict.fromkeys(candidates))


def process_image(
    source_path: Path,
    save_directory: Path,
    index: int,
    raw_url: str,
    options: DownloadOptions,
    maximum_pixels: int,
) -> Path:
    """Decode one source image and atomically publish the selected format."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", PIL.Image.DecompressionBombWarning)
        image = PIL.Image.open(source_path)
        if image.width * image.height > maximum_pixels:
            image.close()
            raise ValueError(f"Image exceeds {maximum_pixels} pixel limit")
        image.load()
    with image:
        detected_format = (image.format or "JPEG").lower()
        target_format = options.image_format.value
        if target_format == "original":
            target_format = "jpg" if detected_format == "jpeg" else detected_format
            matching_extensions = sorted(
                extension
                for extension, image_format in PIL.Image.registered_extensions().items()
                if image_format.lower() == detected_format
            )
            target_extension = ".jpg" if detected_format == "jpeg" else matching_extensions[0]
        else:
            target_extension = f".{target_format}"
        base_name = image_base_name(index, raw_url, options.naming.value, options.naming_version)
        final_path = save_directory / f"{base_name}{target_extension}"
        suffix = 2
        while final_path.exists():
            final_path = save_directory / f"{base_name} ({suffix}){target_extension}"
            suffix += 1
        temporary_path = final_path.with_name(f".{final_path.name}.{uuid.uuid4().hex}.tmp")
        try:
            if options.image_format.value == "original":
                shutil.copyfile(source_path, temporary_path)
            else:
                output_image = image
                if target_format == "jpg" and output_image.mode in ("RGBA", "P"):
                    background = PIL.Image.new("RGB", output_image.size, (255, 255, 255))
                    alpha = output_image.convert("RGBA").getchannel("A")
                    background.paste(output_image, mask=alpha)
                    output_image = background
                elif target_format == "jpg" and output_image.mode != "RGB":
                    output_image = output_image.convert("RGB")
                save_options = {"quality": 95} if target_format == "jpg" else {}
                pillow_format = "JPEG" if target_format == "jpg" else target_format.upper()
                output_image.save(temporary_path, format=pillow_format, **save_options)
            if not is_valid_image(temporary_path, maximum_pixels):
                raise ValueError("Processed image failed integrity validation")
            temporary_path.replace(final_path)
            return final_path
        finally:
            temporary_path.unlink(missing_ok=True)


def current_output_files(
    task: DownloadTask,
    images: list[ImageRecord],
    options: DownloadOptions,
    maximum_pixels: int,
) -> list[Path]:
    """Resolve exactly one validated output file for every image record."""
    output_files: list[Path] = []
    for image in images:
        matching = [path for path in expected_image_paths(task, image, options) if is_valid_image(path, maximum_pixels)]
        if len(matching) != 1:
            raise RuntimeError(f"Expected one output for image {image['image_index']}, found {len(matching)}")
        output_files.append(matching[0])
    return output_files
