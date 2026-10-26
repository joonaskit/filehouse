"""Reads the details of one file on a worker thread, for the details pane."""

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from filehouse.core import properties
from filehouse.core.errors import OperationError
from filehouse.qt_models.workers import Outbox


class _Task(QRunnable):
    def __init__(self, location, outbox):
        super().__init__()
        self._location = location
        self._outbox = outbox

    def run(self):
        try:
            result = properties.read(self._location)
        except OperationError as error:
            result = error
        self._outbox.send("done", result)


class Reading(QObject):
    """One run of `properties.read`. `done` carries the Properties, or the
    OperationError when the file could not be read."""

    done = Signal(object)

    def __init__(self, location, parent=None):
        super().__init__(parent)
        QThreadPool.globalInstance().start(_Task(location, Outbox(self)))
