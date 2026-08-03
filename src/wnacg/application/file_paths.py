"""Safe and deterministic path construction for download artifacts."""

import re
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urlsplit

_INVALID_WINDOWS_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE = re.compile(r"\s+")
_WINDOWS_RESERVED_NAMES = {
    "AUX",
    "CON",
    "NUL",
    "PRN",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_MAX_COMPONENT_LENGTH = 120


def safe_component(value: str, fallback: str) -> str:
    """Return a cross-platform-safe, bounded path component."""
    normalized = unicodedata.normalize("NFKC", value)
    normalized = _INVALID_WINDOWS_CHARACTERS.sub("", normalized)
    normalized = _WHITESPACE.sub(" ", normalized).strip().rstrip(".")
    cleaned = normalized or fallback
    if cleaned.upper() in _WINDOWS_RESERVED_NAMES:
        cleaned = f"{cleaned}_"
    return cleaned[:_MAX_COMPONENT_LENGTH].rstrip(". ") or fallback


def task_directory(download_root: Path, title: str) -> Path:
    """Build the preferred title-only direct child path for a gallery task."""
    safe_title = safe_component(title, "untitled")
    return download_root.expanduser().resolve() / safe_title


def image_base_name(index: int, raw_url: str, naming: str, naming_version: int) -> str:
    """Build an image basename while preserving recognition of prefixed legacy files."""
    sequence = f"{index + 1:04d}"
    if naming != "original" or not raw_url:
        return sequence

    url_name = Path(unquote(urlsplit(raw_url).path)).stem
    original_name = safe_component(url_name, sequence)
    return f"{sequence}-{original_name}" if naming_version == 2 else original_name


def archive_path(source_directory: Path) -> Path:
    """Return a sibling ZIP path without replacing dots in the directory name."""
    return source_directory.parent / f"{source_directory.name}.zip"


def validated_task_directory(task_path: Path, download_root: Path) -> Path:
    """Return a direct child path that is not a symlink or directory junction."""
    resolved_root = download_root.expanduser().resolve(strict=False)
    expanded_task = task_path.expanduser()
    resolved_parent = expanded_task.parent.resolve(strict=False)
    if not expanded_task.name or resolved_parent != resolved_root:
        raise ValueError(f"Unsafe task directory outside recorded root: {expanded_task}")

    safe_task = resolved_root / expanded_task.name
    if safe_task.is_symlink() or safe_task.is_junction():
        raise ValueError(f"Unsafe task directory is a filesystem link: {safe_task}")
    return safe_task


def prepare_task_directory(task_path: Path, download_root: Path) -> Path:
    """Create and revalidate an application-owned task directory before writes."""
    safe_task = validated_task_directory(task_path, download_root)
    safe_task.mkdir(parents=True, exist_ok=True)
    return validated_task_directory(safe_task, download_root)
