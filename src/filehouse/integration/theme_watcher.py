"""Follows the shell's theme file while the app runs.

The shell replaces `theme.json` atomically, so a watch on the file alone is lost
at the first change: the directory is watched too, or its parent while the shell
has never run and the directory isn't there. A change is read after a short while,
since a write can arrive as several events.

`adjust` is given each theme read and returns the one to apply: the look chosen by
hand laid over it (`appearance.theme_with`).
"""

import json
import os

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer

from filehouse.integration import style, theme

_SETTLE_MS = 200


class ThemeWatcher(QObject):
    def __init__(self, app, path=None, settle_ms=_SETTLE_MS, parent=None, adjust=None):
        super().__init__(parent)
        self._app = app
        self._adjust = adjust or (lambda read: read)
        self._path = os.fspath(path or theme.theme_path())
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_changed)
        self._watcher.fileChanged.connect(self._on_changed)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(settle_ms)
        self._timer.timeout.connect(self._reload)
        self._watch()

    def _watch(self):
        """Watch what exists: the file, its directory, or the directory above that."""
        directory = os.path.dirname(self._path)
        wanted = [directory if os.path.isdir(directory) else os.path.dirname(directory)]
        if os.path.exists(self._path):
            wanted.append(self._path)
        watched = set(self._watcher.directories() + self._watcher.files())
        stale = [path for path in watched if path not in wanted]
        if stale:
            self._watcher.removePaths(stale)
        missing = [path for path in wanted if path not in watched and os.path.exists(path)]
        if missing:
            self._watcher.addPaths(missing)

    def _on_changed(self, _path):
        self._timer.start()

    def _reload(self):
        # A replaced file is a new one to the watcher: watch it again
        self._watch()
        # A file that is gone or not readable (the shell is mid-write, or was removed)
        # leaves the look as it is: the defaults are for a start without a theme
        try:
            with open(self._path, encoding="utf-8") as f:
                json.load(f)
        except (OSError, ValueError):
            return
        style.apply(self._app, self._adjust(theme.load_theme(self._path)), live=True)
