"""Small tools for the UI tests."""

import threading
import time
from contextlib import contextmanager

from gi.repository import Gio
from PySide6.QtCore import QRunnable, QThread, QThreadPool
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

TIMEOUT = 5000


def gfile(path):
    return Gio.File.new_for_path(str(path))


class _Hold(QRunnable):
    def __init__(self, release):
        super().__init__()
        self._release = release

    def run(self):
        self._release.wait(30)


@contextmanager
def shared_threads_held():
    """Every thread of the shared pool busy, as with thumbnails, counts or a search that
    take long, until the block ends. Whatever else is started on it meanwhile waits."""
    pool = QThreadPool.globalInstance()
    release = threading.Event()
    for _ in range(pool.maxThreadCount()):
        pool.start(_Hold(release))
    try:
        yield
    finally:
        release.set()
        pool.waitForDone(TIMEOUT)


def settle(window, timeout=TIMEOUT):
    """Wait until what the window was asked to do to files at once (a new folder, a
    rename, a link, what a drop is, taking a rename back) is done, and its answer shown,
    and what it was asked to look at (a typed path, what is dragged over it)."""
    deadline = time.monotonic() + timeout / 1000
    while (
        window._file_actions.busy
        or window._jobs.busy
        or window._background.busy
        or window._sidebar.busy
    ):
        assert time.monotonic() < deadline, "the window's file actions are still busy"
        QTest.qWait(5)


def started_by(window, action):
    """Do `action()`, then `settle`: the job it started, or None when it started none."""
    started = []

    def add(job):
        started.append(job)

    window._jobs.job_added.connect(add)
    try:
        action()
        settle(window)
    finally:
        window._jobs.job_added.disconnect(add)
    return started[-1] if started else None


class Stuck:
    """Stands in for a file system call that does not come back until `release()`, as
    on a network share that has stopped answering. Then it does what it stands for.
    The `stuck` fixture puts one in place."""

    def __init__(self, real):
        self._real = real
        self._go = threading.Event()
        self.threads = []

    def __call__(self, *args):
        self.threads.append(QThread.currentThread())
        self._go.wait(TIMEOUT / 1000)
        return self._real(*args)

    def release(self):
        self._go.set()


def off_the_ui_thread(call):
    """Whether a `Stuck` was called, and never on the UI thread."""
    return bool(call.threads) and QApplication.instance().thread() not in call.threads
