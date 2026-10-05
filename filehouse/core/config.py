"""Application configuration and persistent settings manager."""

from pathlib import Path
from PySide6.QtCore import QSettings


class AppConfig:
    """Wrapper around QSettings for typed preference management."""

    ORGANIZATION = "FileHouse"
    APPLICATION = "FileHouse"

    def __init__(self):
        self.settings = QSettings(self.ORGANIZATION, self.APPLICATION)

    @property
    def last_directory(self) -> Path:
        saved = self.settings.value("last_directory", str(Path.home()))
        path = Path(saved)
        return path if path.is_dir() else Path.home()

    @last_directory.setter
    def last_directory(self, path: Path | str) -> None:
        self.settings.setValue("last_directory", str(Path(path).resolve()))

    @property
    def show_hidden_files(self) -> bool:
        return self.settings.value("show_hidden_files", False, type=bool)

    @show_hidden_files.setter
    def show_hidden_files(self, value: bool) -> None:
        self.settings.setValue("show_hidden_files", bool(value))

    @property
    def show_preview_panel(self) -> bool:
        return self.settings.value("show_preview_panel", True, type=bool)

    @show_preview_panel.setter
    def show_preview_panel(self, value: bool) -> None:
        self.settings.setValue("show_preview_panel", bool(value))

    def save_window_state(self, geometry: bytes, state: bytes) -> None:
        self.settings.setValue("window_geometry", geometry)
        self.settings.setValue("window_state", state)

    def restore_window_state(self) -> tuple[bytes | None, bytes | None]:
        geom = self.settings.value("window_geometry")
        state = self.settings.value("window_state")
        return geom, state
