"""The threads file operations run on. Nothing else in the app starts threads for the core."""

import logging

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

log = logging.getLogger(__name__)

# Separate from the global pool, so a job waiting for an answer never holds up a folder listing
_pool = QThreadPool()
_pool.setMaxThreadCount(8)

# What a window does to files at once (a new folder, a rename, a link): one at a time, in
# the order it was asked for, so that a rename never overtakes the folder it renames
_in_turn = QThreadPool()
_in_turn.setMaxThreadCount(1)
# What only looks (where the nearest folder is that is there): a few at once, in any order,
# so that a folder that does not answer holds up none of the above
_any_order = QThreadPool()
_any_order.setMaxThreadCount(4)


def spawn(function):
    """Run `function` on a worker thread."""
    _pool.start(function)


def active_count():
    """How many worker threads are busy."""
    return _pool.activeThreadCount()


class _Signals(QObject):
    # done, failed, the result, the exception
    finished = Signal(object, object, object, object)


class _Call(QRunnable):
    def __init__(self, function, done, failed, signals):
        super().__init__()
        self._function = function
        self._done = done
        self._failed = failed
        self._signals = signals

    def run(self):
        result = error = None
        try:
            result = self._function()
        except Exception as exception:
            error = exception
        try:
            self._signals.finished.emit(self._done, self._failed, result, error)
        except RuntimeError:
            # Nobody is waiting for the answer any more. What was done stays done
            pass


class Background(QObject):
    """Runs a short piece of file work off the UI thread, for what a window does at once.

    `call(function, done, failed)` runs `function()` on a worker, then calls `done(result)`,
    or `failed(exception)` when it raised, on this object's thread. Calls run one at a
    time, in the order they were made, across the whole app; with `in_turn` False, for
    what changes nothing, a few at once and in any order."""

    def __init__(self, parent=None, in_turn=True):
        super().__init__(parent)
        self._pool = _in_turn if in_turn else _any_order
        self.pending = 0
        self._signals = _Signals(self)
        self._signals.finished.connect(self._on_finished)

    @property
    def busy(self):
        return self.pending > 0

    def call(self, function, done, failed=None):
        self.pending += 1
        self._pool.start(_Call(function, done, failed, self._signals))

    def _on_finished(self, done, failed, result, error):
        self.pending -= 1
        if error is None:
            done(result)
        elif failed is not None:
            failed(error)
        else:
            log.error("background work failed", exc_info=error)
