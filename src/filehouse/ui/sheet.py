"""A panel that slides up from the bottom of the window and down again."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from filehouse.integration import style
from filehouse.ui.slide import Slide


class Sheet(QWidget):
    """Holds one widget. It sits in the window's layout, below the list, so what
    it shows pushes the list up and covers nothing."""

    opened_changed = Signal(bool)
    settled = Signal()  # it has finished sliding

    def __init__(self, content, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(style.px(8), style.px(4), style.px(8), style.px(4))
        layout.addWidget(content)
        self._slide = Slide(self)
        self._slide.settled.connect(self.settled)

    @property
    def is_open(self):
        return self._slide.shown

    def set_open(self, open_, animate=True):
        if open_ == self.is_open:
            return
        self._slide.set_shown(open_, animate)
        self.opened_changed.emit(open_)
