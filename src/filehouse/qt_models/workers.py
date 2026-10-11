"""The threads file operations run on. Nothing else in the app starts threads for the core."""

from PySide6.QtCore import QThreadPool

# Separate from the global pool, so a job waiting for an answer never holds up a folder listing
_pool = QThreadPool()
_pool.setMaxThreadCount(8)


def spawn(function):
    """Run `function` on a worker thread."""
    _pool.start(function)


def active_count():
    """How many worker threads are busy."""
    return _pool.activeThreadCount()
