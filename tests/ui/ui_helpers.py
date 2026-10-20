"""Small tools for the UI tests."""

import threading
from contextlib import contextmanager

from gi.repository import Gio
from PySide6.QtCore import QRunnable, QThreadPool

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
