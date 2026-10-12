"""Reads the details of one file on a worker thread, for the details pane."""

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from filehouse.core import properties
from filehouse.core.errors import OperationError


class _Signals(QObject):
    done = Signal(object)


class _Task(QRunnable):
    def __init__(self, location, signals):
        super().__init__()
        self._location = location
        self._signals = signals

    def run(self):
        try:
            result = properties.read(self._location)
        except OperationError as error:
            result = error
        try:
            self._signals.done.emit(result)
        except RuntimeError:
            # Nobody is waiting for the answer any more
            pass


class Reading(QObject):
    """One run of `properties.read`. `done` carries the Properties, or the
    OperationError when the file could not be read."""

    done = Signal(object)

    def __init__(self, location, parent=None):
        super().__init__(parent)
        signals = _Signals(self)
        signals.done.connect(self.done)
        QThreadPool.globalInstance().start(_Task(location, signals))
