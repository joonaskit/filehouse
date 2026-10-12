"""The search field of the toolbar: what is typed filters the list and searches below."""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLineEdit

from filehouse.integration import style


class SearchField(QLineEdit):
    cancelled = Signal()  # Escape: the search is over
    left = Signal()  # Down or Return: on to what was found

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("Search")
        self.setClearButtonEnabled(True)
        self.setFixedHeight(style.px(style.dense("control_height")))
        self._icon = self.addAction(self._find_icon(), QLineEdit.ActionPosition.LeadingPosition)

    @staticmethod
    def _find_icon():
        return style.icon("edit-find-symbolic", "edit-find", colour="textSecondary")

    def retheme(self):
        self._icon.setIcon(self._find_icon())

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.cancelled.emit()
        elif key in (Qt.Key.Key_Down, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.left.emit()
        else:
            super().keyPressEvent(event)
