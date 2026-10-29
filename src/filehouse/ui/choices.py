"""The settings' choices of the look, drawn as the shell's: a few buttons in a rounded
well (Dark or Light, the corners) and a row of colour dots (the accent). The same as
Lumina's."""

from PySide6.QtCore import QRectF, QSize, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from filehouse.integration import style

# A dot's size, and how much smaller its colour is than its ring when chosen
_DOT = 28
_RING_GAP = 6


class Segments(QFrame):
    """One of a few choices, each a button in a rounded well; the one chosen is raised.
    `choices` are (key, label)."""

    chosen = Signal(str)

    def __init__(self, choices, parent=None):
        super().__init__(parent)
        self.setObjectName("segments")
        row = QHBoxLayout(self)
        padding = style.px(style.SEGMENT_PADDING)
        row.setContentsMargins(padding, padding, padding, padding)
        row.setSpacing(style.px(2))
        self._group = QButtonGroup(self)
        self._buttons = {}
        for key, label in choices:
            button = QPushButton(label)
            button.setCheckable(True)
            self._group.addButton(button)
            row.addWidget(button, 1)
            self._buttons[key] = button
            button.clicked.connect(lambda _on, key=key: self.chosen.emit(key))

    def value(self):
        return next((key for key, button in self._buttons.items() if button.isChecked()), None)

    def set_value(self, key):
        """Show `key` as chosen; signals nothing."""
        self._buttons[key].setChecked(True)

    def button(self, key):
        return self._buttons[key]


class _Dot(QAbstractButton):
    """A colour of the theme's palette, by name; ringed in it when chosen."""

    def __init__(self, name, label, parent=None):
        super().__init__(parent)
        self.name = name
        self.setCheckable(True)
        self.setToolTip(label)
        self.setAccessibleName(label)
        self.setFixedSize(self.sizeHint())

    def sizeHint(self):
        return QSize(style.px(_DOT), style.px(_DOT))

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colour = style.palette_color(self.name)
        if not self.isEnabled():
            colour.setAlphaF(0.4)
        bounds = QRectF(self.rect())
        width = style.px(2)
        if self.isChecked():
            painter.setPen(QPen(colour, width))
            half = width / 2
            painter.drawEllipse(bounds.adjusted(half, half, -half, -half))
            inset = style.px(_RING_GAP)
        else:
            # As large as a chosen one's ring, a little in from the edge
            inset = style.px(_RING_GAP) - width
        painter.setPen(QPen(colour.darker(115) if self.underMouse() else colour, 0))
        painter.setBrush(colour)
        painter.drawEllipse(bounds.adjusted(inset, inset, -inset, -inset))

    def enterEvent(self, event):
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.update()
        super().leaveEvent(event)


class AccentDots(QWidget):
    """The accents as a row of dots, and the chosen one's name after them. `accents` are
    (name, label), the names those of the theme's palette."""

    chosen = Signal(str)

    def __init__(self, accents, parent=None):
        super().__init__(parent)
        self._labels = dict(accents)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(style.px(4))
        self._group = QButtonGroup(self)
        self._dots = {}
        for name, label in accents:
            dot = _Dot(name, label)
            self._group.addButton(dot)
            row.addWidget(dot)
            self._dots[name] = dot
            dot.clicked.connect(lambda _on, name=name: self._chosen(name))
        self.name = QLabel()
        self.name.setObjectName("secondary")
        row.addSpacing(style.px(6))
        row.addWidget(self.name)
        row.addStretch(1)

    def value(self):
        return next((name for name, dot in self._dots.items() if dot.isChecked()), None)

    def set_value(self, name):
        """Show `name` as chosen; signals nothing."""
        self._dots[name].setChecked(True)
        self.name.setText(self._labels[name])

    def dot(self, name):
        return self._dots[name]

    def _chosen(self, name):
        self.name.setText(self._labels[name])
        self.chosen.emit(name)
