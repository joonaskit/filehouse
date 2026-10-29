"""Menus in the app's look: rounded, with one-colour icons."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu

from filehouse.integration import style
from filehouse.ui import glass


def menu(parent, title=None):
    """A QMenu whose rounded corners show: without this the popup's own square
    window is painted behind them. On glass it is blurred behind, inside the corners."""
    made = QMenu(title, parent) if title else QMenu(parent)
    made.setWindowFlag(Qt.WindowType.FramelessWindowHint)
    made.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint)
    style.translucent(made, rounded=True)
    glass.light(made)
    return made
