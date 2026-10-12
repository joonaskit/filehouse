"""The user's favourite folders: the places Dolphin keeps, as the app shows them.

One list for the process, read from `user-places.xbel` and read again when another app
(Dolphin, a file dialog) changes the file.
"""

import os
from dataclasses import dataclass, field

from gi.repository import Gio
from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

from filehouse.core import locations
from filehouse.integration import places

_SETTLE_MS = 150


@dataclass(frozen=True)
class Favourite:
    id: str
    title: str
    # Not compared: two reads of the same file make two objects for one folder
    location: Gio.File = field(compare=False)
    icon: str = "folder"

    @property
    def uri(self):
        return self.location.get_uri()


class Favourites(QObject):
    changed = Signal()

    def __init__(self, path=None, settle_ms=_SETTLE_MS, parent=None):
        super().__init__(parent)
        self._file = places.PlacesFile(path)
        self._items = []
        self._watcher = QFileSystemWatcher(self)
        self._watcher.directoryChanged.connect(self._on_file_changed)
        self._watcher.fileChanged.connect(self._on_file_changed)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(settle_ms)
        self._timer.timeout.connect(self.reload)
        self._items = self._read()
        self._watch()

    @property
    def items(self):
        return list(self._items)

    @property
    def available(self):
        """Whether there is a places file to keep favourites in: Dolphin makes it."""
        return self._file.exists

    def find(self, location):
        """The favourite for a folder, or None."""
        uri = location.get_uri()
        return next((item for item in self._items if _same(item.location, location, uri)), None)

    # Changes. Each says whether the file was written

    def add(self, location, title=None):
        title = title or locations.display_name(location)
        done = self._file.add(location.get_uri(), title)
        return self._after(done)

    def remove(self, favourite):
        return self._after(self._file.remove(favourite.id))

    def rename(self, favourite, title):
        return self._after(self._file.rename(favourite.id, title))

    def move(self, favourite, offset):
        return self._after(self._file.move(favourite.id, offset))

    def _after(self, done):
        if done:
            self.reload()
        return done

    # The file

    def reload(self):
        self._watch()
        items = self._read()
        if items != self._items:
            self._items = items
            self.changed.emit()

    def _read(self):
        found = self._file.read() or []
        return [
            Favourite(place.id, place.title or _name(place.uri), _location(place.uri), place.icon)
            for place in found
            if place.uri
        ]

    def _watch(self):
        """The file, and its folder: a file that is replaced is a new one to the watcher."""
        folder = os.path.dirname(self._file.path)
        wanted = [path for path in (folder, self._file.path) if os.path.exists(path)]
        watched = set(self._watcher.directories() + self._watcher.files())
        stale = [path for path in watched if path not in wanted]
        if stale:
            self._watcher.removePaths(stale)
        missing = [path for path in wanted if path not in watched]
        if missing:
            self._watcher.addPaths(missing)

    def _on_file_changed(self, _path):
        self._timer.start()


def _location(uri):
    return Gio.File.new_for_uri(uri)


def _name(uri):
    return locations.display_name(_location(uri))


def _same(a, b, uri):
    return a.get_uri() == uri or a.equal(b)
