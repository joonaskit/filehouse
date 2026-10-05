"""Address bar and navigation controls widget."""

from pathlib import Path
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget,
    QHBoxLayout,
    QLineEdit,
    QToolButton,
    QStyle,
)


class AddressBar(QWidget):
    """Navigation toolbar containing back, forward, up, home buttons and address line."""

    navigate_requested = Signal(Path)
    back_requested = Signal()
    forward_requested = Signal()
    up_requested = Signal()
    home_requested = Signal()
    refresh_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        # Navigation buttons using standard Qt icons
        self.btn_back = QToolButton(self)
        self.btn_back.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowBack))
        self.btn_back.setToolTip("Back")
        self.btn_back.clicked.connect(self.back_requested.emit)

        self.btn_forward = QToolButton(self)
        self.btn_forward.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowForward))
        self.btn_forward.setToolTip("Forward")
        self.btn_forward.clicked.connect(self.forward_requested.emit)

        self.btn_up = QToolButton(self)
        self.btn_up.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowUp))
        self.btn_up.setToolTip("Up to parent folder")
        self.btn_up.clicked.connect(self.up_requested.emit)

        self.btn_home = QToolButton(self)
        self.btn_home.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirHomeIcon))
        self.btn_home.setToolTip("Home Directory")
        self.btn_home.clicked.connect(self.home_requested.emit)

        self.btn_refresh = QToolButton(self)
        self.btn_refresh.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.btn_refresh.setToolTip("Refresh")
        self.btn_refresh.clicked.connect(self.refresh_requested.emit)

        # Path input line edit
        self.path_edit = QLineEdit(self)
        self.path_edit.setPlaceholderText("Enter directory path...")
        self.path_edit.returnPressed.connect(self._on_enter_pressed)

        # Assemble layout
        layout.addWidget(self.btn_back)
        layout.addWidget(self.btn_forward)
        layout.addWidget(self.btn_up)
        layout.addWidget(self.btn_home)
        layout.addWidget(self.path_edit)
        layout.addWidget(self.btn_refresh)

    def set_path(self, path: Path | str) -> None:
        """Update address bar text without triggering navigation."""
        self.path_edit.setText(str(path))

    def set_nav_states(self, can_back: bool, can_forward: bool) -> None:
        """Enable or disable back/forward buttons."""
        self.btn_back.setEnabled(can_back)
        self.btn_forward.setEnabled(can_forward)

    def _on_enter_pressed(self):
        text = self.path_edit.text().strip()
        if text:
            target = Path(text).expanduser()
            self.navigate_requested.emit(target)
