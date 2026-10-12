"""A panel that slides up from the bottom of the window and down again."""

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Signal
from PySide6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from filehouse.integration import style

_SLIDE_MS = 160
_NO_LIMIT = 16777215


class Sheet(QWidget):
    """Holds one widget. It sits in the window's layout, below the list, so what
    it shows pushes the list up and covers nothing."""

    opened_changed = Signal(bool)
    settled = Signal()  # it has finished sliding

    def __init__(self, content, parent=None):
        super().__init__(parent)
        self._open = False
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(style.px(8), style.px(4), style.px(8), style.px(4))
        layout.addWidget(content)
        self._slide = QPropertyAnimation(self, b"maximumHeight", self)
        self._slide.setDuration(_SLIDE_MS)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._slide.finished.connect(self._on_slid)
        self.hide()

    @property
    def is_open(self):
        return self._open

    def set_open(self, open_, animate=True):
        if open_ == self._open:
            return
        self._open = open_
        self._slide.stop()
        if not animate:
            self.setMaximumHeight(_NO_LIMIT)
            self.setVisible(open_)
            self.settled.emit()
        else:
            start = self.height() if self.isVisible() else 0
            if open_:
                self.setMaximumHeight(start)
                self.show()
            self._slide.setStartValue(start)
            self._slide.setEndValue(self.sizeHint().height() if open_ else 0)
            self._slide.start()
        self.opened_changed.emit(open_)

    def _on_slid(self):
        if not self._open:
            self.hide()
        # Free to follow its content from here on: what it shows changes height
        self.setMaximumHeight(_NO_LIMIT)
        self.settled.emit()
