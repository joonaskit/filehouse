"""Small tools for the UI tests."""

import threading
import time
from contextlib import contextmanager

from gi.repository import Gio
from PySide6.QtCore import QRunnable, QThreadPool
from PySide6.QtTest import QTest

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
    rename, a link, what a drop is) is done, and its answer shown."""
    deadline = time.monotonic() + timeout / 1000
    while window._file_actions.busy:
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
