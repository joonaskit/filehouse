"""The sign that the app runs, where the desktop keeps such signs: the system tray, or
the shell's row of running apps. It is how a process without a window is seen, opened
and ended.

Qt registers it as a StatusNotifierItem of the "application status" kind, which is what
the shell shows. Without a tray nothing is shown and nothing is lost.
"""

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

_TITLE = "Filehouse"


class Tray(QObject):
    # A click on the icon: the window, or one when there is none
    open_requested = Signal()
    new_window_requested = Signal()
    quit_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # The icon the windows have
        self._icon = QSystemTrayIcon(QIcon.fromTheme("system-file-manager"), self)
        self._icon.setToolTip(_TITLE)
        # Drawn by the tray and not by the app: no icons and no look of ours
        self._menu = QMenu()
        self.new_window = self._menu.addAction("New window")
        self.new_window.triggered.connect(self.new_window_requested)
        self._menu.addSeparator()
        self.quit = self._menu.addAction(f"Quit {_TITLE}")
        self.quit.triggered.connect(self.quit_requested)
        self._icon.setContextMenu(self._menu)
        self._icon.activated.connect(self._on_activated)

    def set_shown(self, shown):
        self._icon.setVisible(bool(shown))

    @property
    def shown(self):
        return self._icon.isVisible()

    def _on_activated(self, reason):
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self.open_requested.emit()
