"""Applies a permissions preset on a worker thread: a selection can be thousands of files."""

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from filehouse.core.operations import permissions


class _Signals(QObject):
    done = Signal(object)


class _Task(QRunnable):
    def __init__(self, preset, selected, signals):
        super().__init__()
        self._preset = preset
        self._selected = selected
        self._signals = signals

    def run(self):
        outcome = permissions.apply(self._preset, self._selected)
        try:
            self._signals.done.emit(outcome)
        except RuntimeError:
            # Nobody is waiting for the answer any more. The change is made all the same
            pass


class PermissionChange(QObject):
    """One run of `permissions.apply`. `done` carries its Outcome."""

    done = Signal(object)

    def __init__(self, preset, selected, parent=None):
        super().__init__(parent)
        signals = _Signals(self)
        signals.done.connect(self.done)
        QThreadPool.globalInstance().start(_Task(preset, list(selected), signals))
