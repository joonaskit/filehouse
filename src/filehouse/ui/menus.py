"""Menus in the app's look: rounded, with one-colour icons."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu


def menu(parent, title=None):
    """A QMenu whose rounded corners show: without this the popup's own square
    window is painted behind them."""
    made = QMenu(title, parent) if title else QMenu(parent)
    made.setWindowFlag(Qt.WindowType.FramelessWindowHint)
    made.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint)
    made.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    return made
