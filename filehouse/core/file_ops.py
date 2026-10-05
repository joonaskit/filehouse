"""File operations and formatting utilities."""

from pathlib import Path
import os
import mimetypes
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl


def format_size(size_bytes: int) -> str:
    """Format bytes into human-readable string (KB, MB, GB, etc.)."""
    if size_bytes < 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    value = float(size_bytes)
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} PB"


def get_mime_type(path: Path) -> str:
    """Return guessed MIME type for a given path."""
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"


def open_file_with_default_app(path: Path) -> bool:
    """Open a file with system default application via QDesktopServices."""
    if path.exists():
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
    return False
