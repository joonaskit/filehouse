"""Adds up the size of folders on a worker thread, for the properties dialog."""

from gi.repository import Gio
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from filehouse.core import properties
from filehouse.qt_models.workers import Outbox


class _Task(QRunnable):
    def __init__(self, roots, cancellable, outbox):
        super().__init__()
        self._roots = roots
        self._cancellable = cancellable
        self._outbox = outbox

    def run(self):
        totals = properties.measure(
            self._roots, self._cancellable, lambda totals: self._outbox.send("progress", totals)
        )
        if totals is not None:
            self._outbox.send("finished", totals)


class Measurement(QObject):
    """One run of `properties.measure`. `progress` and `finished` carry Totals."""

    progress = Signal(object)
    finished = Signal(object)

    def __init__(self, roots, parent=None):
        super().__init__(parent)
        self._cancellable = Gio.Cancellable()
        # Through a plain function: Qt can't call a Gio method directly
        cancellable = self._cancellable
        self.destroyed.connect(lambda: cancellable.cancel())
        QThreadPool.globalInstance().start(_Task(list(roots), self._cancellable, Outbox(self)))

    def cancel(self):
        self._cancellable.cancel()
