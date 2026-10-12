"""Adds up the size of folders on a worker thread, for the properties dialog."""

from gi.repository import Gio
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from filehouse.core import properties


class _Signals(QObject):
    progress = Signal(object)
    finished = Signal(object)


class _Task(QRunnable):
    def __init__(self, roots, cancellable, signals):
        super().__init__()
        self._roots = roots
        self._cancellable = cancellable
        self._signals = signals

    def run(self):
        try:
            totals = properties.measure(self._roots, self._cancellable, self._signals.progress.emit)
            if totals is not None:
                self._signals.finished.emit(totals)
        except RuntimeError:
            # Nobody is waiting for the answer any more
            pass


class Measurement(QObject):
    """One run of `properties.measure`. `progress` and `finished` carry Totals."""

    progress = Signal(object)
    finished = Signal(object)

    def __init__(self, roots, parent=None):
        super().__init__(parent)
        self._cancellable = Gio.Cancellable()
        signals = _Signals(self)
        signals.progress.connect(self.progress)
        signals.finished.connect(self.finished)
        # Through a plain function: Qt can't call a Gio method directly
        cancellable = self._cancellable
        self.destroyed.connect(lambda: cancellable.cancel())
        QThreadPool.globalInstance().start(_Task(list(roots), self._cancellable, signals))

    def cancel(self):
        self._cancellable.cancel()
