"""Cross-platform local-path opening through Qt's platform integration."""

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices


def open_local_path(path: Path) -> bool:
    """Open an existing file/folder, or its nearest existing parent."""
    target = path.expanduser().resolve(strict=False)
    while not target.exists() and target != target.parent:
        target = target.parent
    return QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
