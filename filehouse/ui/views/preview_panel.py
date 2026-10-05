"""Preview and metadata panel component."""

from pathlib import Path
from datetime import datetime
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QFrame,
)
from PySide6.QtGui import QPixmap

from filehouse.core.file_ops import format_size, get_mime_type

TEXT_EXTENSIONS = {
    ".txt", ".py", ".md", ".json", ".yaml", ".yml", ".toml",
    ".sh", ".ini", ".cfg", ".log", ".html", ".css", ".js", ".ts",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg"}


class PreviewPanel(QWidget):
    """Panel displaying file metadata and preview content."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # Title
        self.title_label = QLabel("Preview", self)
        font = self.title_label.font()
        font.setBold(True)
        self.title_label.setFont(font)
        layout.addWidget(self.title_label)

        # File metadata info labels
        self.info_label = QLabel("Select an item to preview details.", self)
        self.info_label.setWordWrap(True)
        self.info_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.info_label)

        # Image preview
        self.image_preview = QLabel(self)
        self.image_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_preview.setMinimumHeight(120)
        self.image_preview.setFrameShape(QFrame.Shape.StyledPanel)
        self.image_preview.hide()
        layout.addWidget(self.image_preview)

        # Text preview
        self.text_preview = QPlainTextEdit(self)
        self.text_preview.setReadOnly(True)
        self.text_preview.hide()
        layout.addWidget(self.text_preview)

        layout.addStretch()

    def update_preview(self, path: Path | None) -> None:
        """Update the panel contents for the selected file or directory."""
        if not path or not path.exists():
            self.title_label.setText("Preview")
            self.info_label.setText("No item selected.")
            self.image_preview.hide()
            self.text_preview.hide()
            return

        self.title_label.setText(path.name or str(path))

        try:
            stat = path.stat()
            modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            size_str = format_size(stat.st_size) if path.is_file() else "Folder"
            mime = get_mime_type(path) if path.is_file() else "Directory"

            info = (
                f"<b>Path:</b> {path}<br>"
                f"<b>Type:</b> {mime}<br>"
                f"<b>Size:</b> {size_str}<br>"
                f"<b>Modified:</b> {modified}"
            )
            self.info_label.setText(info)
        except OSError as err:
            self.info_label.setText(f"Unable to read file info: {err}")
            self.image_preview.hide()
            self.text_preview.hide()
            return

        if path.is_dir():
            self.image_preview.hide()
            self.text_preview.hide()
            return

        suffix = path.suffix.lower()

        # Handle image preview
        if suffix in IMAGE_EXTENSIONS:
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    200, 200,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.image_preview.setPixmap(scaled)
                self.image_preview.show()
                self.text_preview.hide()
                return

        # Handle text preview
        if suffix in TEXT_EXTENSIONS or get_mime_type(path).startswith("text/"):
            try:
                # Read up to 4KB of text
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    sample = f.read(4096)
                self.text_preview.setPlainText(sample)
                self.text_preview.show()
                self.image_preview.hide()
                return
            except OSError:
                pass

        self.image_preview.hide()
        self.text_preview.hide()
