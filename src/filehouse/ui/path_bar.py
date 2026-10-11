"""Breadcrumbs that turn into a text field on click or Ctrl+L."""

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QWidget,
)

from filehouse.integration import style


class _Crumbs(QWidget):
    background_clicked = Signal()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.background_clicked.emit()


class _PathEdit(QLineEdit):
    cancelled = Signal()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
        else:
            super().keyPressEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.cancelled.emit()


class PathBar(QStackedWidget):
    location_chosen = Signal(object)  # a location from a breadcrumb
    text_entered = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = ""

        self._crumbs = _Crumbs()
        self._crumbs.background_clicked.connect(self.edit)
        self._layout = QHBoxLayout(self._crumbs)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(style.px(2))

        # Deep paths scroll instead of forcing the window wider
        self._scroll = QScrollArea()
        self._scroll.setWidget(self._crumbs)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.viewport().setAutoFillBackground(False)
        self._crumbs.setAutoFillBackground(False)

        self._edit = _PathEdit()
        self._edit.returnPressed.connect(self._on_return)
        self._edit.cancelled.connect(self._show_crumbs)

        self.addWidget(self._scroll)
        self.addWidget(self._edit)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self):
        # As tall as the buttons in it, or their hover highlight is cut off.
        # A scroll area alone asks for far more
        return QSize(super().sizeHint().width(), style.px(style.CONTROL_HEIGHT))

    def minimumSizeHint(self):
        return QSize(style.px(120), style.px(style.CONTROL_HEIGHT))

    def set_location(self, segments, text):
        """Show `segments`, a root-first list of (name, location). `text` is the editable form."""
        self._text = text
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for position, (name, location) in enumerate(segments):
            if position:
                separator = QLabel("›")
                separator.setEnabled(False)
                self._layout.addWidget(separator)
            button = QToolButton()
            button.setText(name)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setFixedHeight(style.px(style.CONTROL_HEIGHT))
            button.clicked.connect(
                lambda _checked=False, loc=location: self.location_chosen.emit(loc)
            )
            self._layout.addWidget(button)
        self._layout.addStretch(1)
        self._show_crumbs()
        # The current folder is the right end; keep it in view once laid out
        QTimer.singleShot(0, self, self._scroll_to_end)

    def edit(self):
        self._edit.setText(self._text)
        self.setCurrentWidget(self._edit)
        self._edit.setFocus()
        self._edit.selectAll()

    def _scroll_to_end(self):
        bar = self._scroll.horizontalScrollBar()
        bar.setValue(bar.maximum())

    def _show_crumbs(self):
        self.setCurrentWidget(self._scroll)

    def _on_return(self):
        text = self._edit.text()
        self._show_crumbs()
        if text.strip():
            self.text_entered.emit(text)
