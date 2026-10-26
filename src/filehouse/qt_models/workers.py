"""The threads file operations run on. Nothing else in the app starts threads for the core."""

import itertools
import logging
import weakref

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, SignalInstance
from shiboken6 import isValid

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


class _Relay(QObject):
    """Carries the answers of all workers to the main thread. It lives as long as the
    app, so a worker never sends through an object that a window or dialog closing on
    the main thread can delete at the same moment (#94)."""

    # The outbox, the name of its owner's method or signal, the arguments
    posted = Signal(object, str, object)

    def __init__(self):
        super().__init__()
        # Queued: this object is on the main thread, the sender on a worker
        self.posted.connect(self._deliver)

    def _deliver(self, outbox, name, arguments):
        # On the main thread, where owners are deleted too, so this cannot race one
        owner = outbox.owner()
        if owner is None or not isValid(owner):
            # Nobody is waiting for the answer any more. What was done stays done
            return
        target = getattr(owner, name)
        (target.emit if isinstance(target, SignalInstance) else target)(*arguments)


_relay = None


class Outbox:
    """How a worker answers `owner`, a QObject on the main thread, when the owner may be
    gone by then. `send(name, *arguments)` may be called on any thread: it calls the
    owner's method `name`, or emits its signal `name`, on the main thread, and nothing
    at all when the owner has been destroyed. The worker holds no reference to the owner
    or to anything the owner deletes. Made on the main thread, as owners are."""

    def __init__(self, owner):
        global _relay
        if _relay is None:
            _relay = _Relay()
        self.owner = weakref.ref(owner)

    def send(self, name, *arguments):
        _relay.posted.emit(self, name, arguments)


class _Call(QRunnable):
    def __init__(self, function, number, outbox):
        super().__init__()
        self._function = function
        self._number = number
        self._outbox = outbox

    def run(self):
        result = error = None
        try:
            result = self._function()
        except Exception as exception:
            error = exception
        self._outbox.send("_on_finished", self._number, result, error)


class Background(QObject):
    """Runs a short piece of file work off the UI thread, for what a window does at once.

    `call(function, done, failed)` runs `function()` on a worker, then calls `done(result)`,
    or `failed(exception)` when it raised, on the main thread. Calls run one at a
    time, in the order they were made, across the whole app; with `in_turn` False, for
    what changes nothing, a few at once and in any order."""

    def __init__(self, parent=None, in_turn=True):
        super().__init__(parent)
        self._pool = _in_turn if in_turn else _any_order
        self._outbox = Outbox(self)
        # What to call with each answer, by the number of its call. Kept here, on this
        # thread, so a worker never holds the last reference to a window's method
        self._waiting = {}
        self._numbers = itertools.count()

    @property
    def busy(self):
        return bool(self._waiting)

    def call(self, function, done, failed=None):
        number = next(self._numbers)
        self._waiting[number] = (done, failed)
        self._pool.start(_Call(function, number, self._outbox))

    def _on_finished(self, number, result, error):
        done, failed = self._waiting.pop(number)
        if error is None:
            done(result)
        elif failed is not None:
            failed(error)
        else:
            log.error("background work failed", exc_info=error)
