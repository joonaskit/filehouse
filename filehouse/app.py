"""Application entry and lifecycle management."""

import sys
from pathlib import Path
from PySide6.QtWidgets import QApplication

from filehouse.core.config import AppConfig
from filehouse.ui.main_window import MainWindow


def load_stylesheet(app: QApplication) -> None:
    """Load QSS stylesheet if present."""
    qss_path = Path(__file__).parent / "ui" / "styles" / "style.qss"
    if qss_path.exists():
        with open(qss_path, "r", encoding="utf-8") as f:
            app.setStyleSheet(f.read())


def main() -> int:
    """Initialize and run the FileHouse application."""
    app = QApplication(sys.argv)
    app.setApplicationName("FileHouse")
    app.setOrganizationName("FileHouse")

    load_stylesheet(app)

    config = AppConfig()
    window = MainWindow(config)
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
