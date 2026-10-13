"""Showing and hiding a part of the window by sliding it open and shut."""

from PySide6.QtCore import QObject, QTimer, QVariantAnimation, Signal
from PySide6.QtWidgets import QSizePolicy

from filehouse.integration import motion

_NO_LIMIT = 16777215


class Slide(QObject):
    """A widget in a column, shown and hidden by its height: what is above it gives
    and takes the room. It starts hidden."""

    settled = Signal()  # shown or hidden, and no longer moving

    def __init__(self, widget):
        super().__init__(widget)
        self._widget = widget
        self._shown = False
        self._move = motion.animation(widget, b"maximumHeight", motion.SLIDE_EASING, self)
        self._move.finished.connect(self._on_moved)
        widget.hide()

    @property
    def shown(self):
        """Where it is going, which is not where it is while it moves."""
        return self._shown

    def set_shown(self, shown, animate=True):
        if shown == self._shown:
            return
        self._shown = shown
        widget = self._widget
        self._move.stop()
        # Nothing to see of a slide in a window that is not on show
        duration = motion.ms(motion.SLIDE_MS) if animate and widget.window().isVisible() else 0
        if not duration:
            widget.setMaximumHeight(_NO_LIMIT)
            widget.setVisible(shown)
            # Not yet settled: whoever opened it may be about to fill it
            QTimer.singleShot(0, self, self._on_placed)
            return
        start = widget.height() if widget.isVisible() else 0
        if shown:
            widget.setMaximumHeight(start)
            widget.show()
        self._move.setDuration(duration)
        self._move.setStartValue(start)
        self._move.setEndValue(widget.sizeHint().height() if shown else 0)
        self._move.start()

    def _on_placed(self):
        """Shown or hidden without a slide. What is above it gets its new height now,
        and not with the next events: that is what settled means."""
        if self._move.state() == QVariantAnimation.State.Running:
            return
        parent = self._widget.parentWidget()
        if parent is not None and parent.layout() is not None:
            parent.layout().activate()
        self.settled.emit()

    def _on_moved(self):
        if not self._shown:
            self._widget.hide()
        # Free to follow its content from here on: what it shows changes height
        self._widget.setMaximumHeight(_NO_LIMIT)
        self.settled.emit()


class SideSlide(QObject):
    """A widget of a splitter, shown and hidden by its width: the one before it gives
    and takes the room. It remembers how wide it was."""

    def __init__(self, splitter, widget):
        super().__init__(widget)
        self._splitter = splitter
        self._widget = widget
        self._shown = not widget.isHidden()
        self._width = None  # when it was last on show
        self._held = None  # (minimum width, size policy) while it moves
        self._move = QVariantAnimation(self)
        self._move.setEasingCurve(motion.SLIDE_EASING)
        self._move.valueChanged.connect(self._set_width)
        self._move.finished.connect(self._on_moved)

    @property
    def shown(self):
        """Where it is going, which is not where it is while it moves."""
        return self._shown

    def set_shown(self, shown, animate=True):
        if shown == self._shown:
            return
        self._shown = shown
        widget = self._widget
        moving = self._held is not None
        self._move.stop()
        if not shown and not moving and widget.isVisible():
            self._width = self._own_width()
        duration = motion.ms(motion.SLIDE_MS) if animate and widget.window().isVisible() else 0
        if not duration:
            self._release()
            widget.setVisible(shown)
            if shown and self._width:
                self._set_width(self._width)
            return
        start = self._own_width() if widget.isVisible() else 0
        if not moving:
            # A splitter gives a widget its minimum at least: none while it moves
            self._held = (widget.minimumWidth(), widget.sizePolicy())
            policy = widget.sizePolicy()
            policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
            widget.setSizePolicy(policy)
            widget.setMinimumWidth(0)
        if shown:
            widget.show()
            self._set_width(start)
        full = self._width or max(self._held[0], widget.sizeHint().width())
        self._move.setDuration(duration)
        self._move.setStartValue(start)
        self._move.setEndValue(full if shown else 0)
        self._move.start()

    def _index(self):
        return self._splitter.indexOf(self._widget)

    def _own_width(self):
        return self._splitter.sizes()[self._index()]

    def _set_width(self, width):
        sizes = self._splitter.sizes()
        index = self._index()
        both = sizes[index - 1] + sizes[index]
        sizes[index] = min(width, both)
        sizes[index - 1] = both - sizes[index]
        self._splitter.setSizes(sizes)

    def _release(self):
        if self._held is not None:
            width, policy = self._held
            self._held = None
            self._widget.setSizePolicy(policy)
            self._widget.setMinimumWidth(width)

    def _on_moved(self):
        self._release()
        if not self._shown:
            self._widget.hide()
