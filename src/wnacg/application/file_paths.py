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


def task_directory(download_root: Path, title: str, aid: str) -> Path:
    """Build a unique direct child path for a gallery task."""
    safe_aid = safe_component(aid, "unknown")
    safe_title = safe_component(title, safe_aid)
    return download_root.expanduser().resolve() / f"{safe_title} [{safe_aid}]"


def image_base_name(index: int, raw_url: str, naming: str, naming_version: int) -> str:
    """Build a stable image basename while preserving legacy resumability."""
    sequence = f"{index + 1:04d}"
    if naming != "original" or not raw_url:
        return sequence

    url_name = Path(unquote(urlsplit(raw_url).path)).stem
    original_name = safe_component(url_name, sequence)
    if naming_version == 1:
        return original_name
    return f"{sequence}-{original_name}"


def archive_path(source_directory: Path) -> Path:
    """Return a sibling ZIP path without replacing dots in the directory name."""
    return source_directory.parent / f"{source_directory.name}.zip"


def validated_task_directory(task_path: Path, download_root: Path) -> Path:
    """Validate that a destructive task path is a direct child of its recorded root."""
    resolved_root = download_root.expanduser().resolve(strict=False)
    resolved_task = task_path.expanduser().resolve(strict=False)
    if resolved_task == resolved_root or resolved_task.parent != resolved_root:
        raise ValueError(f"Unsafe task directory outside recorded root: {resolved_task}")
    return resolved_task
