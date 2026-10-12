"""Tells the desktop which files were opened from the app, for the lists of recent files.

Two lists, because the desktop has two:

- the freedesktop one, `$XDG_DATA_HOME/recently-used.xbel`, which GTK apps, portals and
  file dialogs read, written with `GBookmarkFile`;
- Plasma's, kept by KActivities, which Kickoff and Dolphin's Recent Files read: told
  over D-Bus, `org.kde.ActivityManager.Resources`.

Nothing is told when the user does not want it: the app's own switch ("Don't remember
opened files") is checked first, then Plasma's own setting, System Settings > Recent
Files (`kactivitymanagerd-pluginsrc`). Only files on this computer count, not folders
and not what is in the trash.

Nothing here can stop a file from opening: it is silent, and the D-Bus calls are
asynchronous.
"""

import configparser
import logging
import os

from gi.repository import Gio, GLib
from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal

from filehouse import APP_ID

log = logging.getLogger(__name__)

# KActivities
BUS_NAME = "org.kde.ActivityManager"
OBJECT_PATH = "/ActivityManager/Resources"
INTERFACE = "org.kde.ActivityManager.Resources"
# What happened to the resource: it was accessed
_ACCESSED = 0
_CALL_TIMEOUT_MS = 2000

# Plasma's setting, from System Settings > Recent Files
_PLUGINS_FILE = "kactivitymanagerd-pluginsrc"
_PLUGINS_GROUP = "Plugin-org.kde.ActivityManager.Resources.Scoring"
_NO_APPLICATIONS, _SPECIFIC_APPLICATIONS = 2, 1

# The list is kept to this many files, the newest
_MAX_ENTRIES = 500


def plasma_allows(app_id=APP_ID, config_dir=None):
    """Whether Plasma's own setting lets this app be remembered: not when it says to
    remember nothing, or this app is excluded, or only some apps are and this is not
    one of them. With no such setting, the answer is yes."""
    path = os.path.join(config_dir or GLib.get_user_config_dir(), _PLUGINS_FILE)
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    try:
        parser.read(path, encoding="utf-8")
        section = parser[_PLUGINS_GROUP]
    except (OSError, UnicodeDecodeError, configparser.Error, KeyError):
        return True
    try:
        what = int(section.get("what-to-remember", "0"))
    except ValueError:
        what = 0
    allowed = _names(section.get("allowed-applications", ""))
    blocked = _names(section.get("blocked-applications", ""))
    if what == _NO_APPLICATIONS or app_id in blocked:
        return False
    by_default = what == _SPECIFIC_APPLICATIONS or section.get("blocked-by-default") == "true"
    return not by_default or app_id in allowed


def _names(text):
    return {name.strip() for name in text.split(",") if name.strip()}


def xbel_path():
    return os.path.join(GLib.get_user_data_dir(), "recently-used.xbel")


class _Write(QRunnable):
    def __init__(self, uri, content_type, app_id, path):
        super().__init__()
        self._arguments = (uri, content_type, app_id, path)

    def run(self):
        uri, content_type, app_id, path = self._arguments
        bookmarks = GLib.BookmarkFile()
        try:
            bookmarks.load_from_file(path)
        except GLib.Error:
            # None yet, or one that can't be read: start a list of our own rather than
            # leave the file as it is, which is what it was a moment ago
            if os.path.exists(path):
                return
        try:
            bookmarks.set_mime_type(uri, content_type or "application/octet-stream")
            bookmarks.set_is_private(uri, False)
            bookmarks.add_application(uri, app_id, f"{app_id} %u")
            self._trim(bookmarks)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            bookmarks.to_file(path)
        except GLib.Error as error:
            log.debug("could not write the recent files: %s", error.message)

    @staticmethod
    def _trim(bookmarks):
        uris = bookmarks.get_uris()
        for uri in sorted(uris, key=lambda uri: bookmarks.get_modified_date_time(uri).to_unix())[
            : max(0, len(uris) - _MAX_ENTRIES)
        ]:
            bookmarks.remove_item(uri)


class _Forget(QRunnable):
    def __init__(self, uris, path, signals):
        super().__init__()
        self._arguments = (uris, path, signals)

    def run(self):
        uris, path, signals = self._arguments
        bookmarks = GLib.BookmarkFile()
        try:
            bookmarks.load_from_file(path)
            for uri in uris:
                if bookmarks.has_item(uri):
                    bookmarks.remove_item(uri)
            bookmarks.to_file(path)
        except GLib.Error as error:
            log.debug("could not take files off the recent files: %s", error.message)
        try:
            signals.forgotten.emit()
        except RuntimeError:
            pass


class _Signals(QObject):
    forgotten = Signal()


class RecentFiles:
    """Records a file as opened. Create it on the main thread and call `start()`.

    `remember()` says whether the user wants it at all: the app's switch."""

    def __init__(
        self,
        remember=lambda: True,
        app_id=APP_ID,
        connection=None,
        path=None,
        plasma=plasma_allows,
    ):
        self._remember = remember
        self._app_id = app_id
        self._connection = connection
        self._path = path or xbel_path()
        self._plasma = plasma
        # One write at a time: each reads the list, adds to it and writes it again
        self._writer = QThreadPool()
        self._writer.setMaxThreadCount(1)
        self._signals = _Signals()

    def start(self):
        if self._connection is None:
            Gio.bus_get(Gio.BusType.SESSION, None, self._on_bus)

    def _on_bus(self, _source, result):
        try:
            self._connection = Gio.bus_get_finish(result)
        except GLib.Error as error:
            log.debug("no session bus, so no recent files for Plasma: %s", error.message)

    @property
    def busy(self):
        return self._writer.activeThreadCount() > 0

    def wanted(self, location):
        """Whether a file would be remembered now."""
        return (
            self._remember()
            and location.is_native()
            and location.get_path() is not None
            and self._is_file(location)
            and self._plasma(self._app_id)
        )

    @staticmethod
    def _is_file(location):
        try:
            kind = location.query_file_type(Gio.FileQueryInfoFlags.NONE, None)
        except GLib.Error:
            return False
        return kind == Gio.FileType.REGULAR

    def add(self, location, content_type=None):
        """Remember a file that was opened, if that is wanted. Whether it was told."""
        if not self.wanted(location):
            return False
        uri = location.get_uri()
        self._writer.start(_Write(uri, content_type, self._app_id, self._path))
        self._tell_plasma(uri, content_type)
        return True

    def forget(self, locations, done=None):
        """Take files off the freedesktop list, which is the one the app shows. The files
        themselves are not touched, and neither is Plasma's own history. `done()` is
        called on this thread when it is written."""
        if done is not None:
            self._signals.forgotten.connect(done, Qt.ConnectionType.SingleShotConnection)
        self._writer.start(
            _Forget([location.get_uri() for location in locations], self._path, self._signals)
        )

    def _tell_plasma(self, uri, content_type):
        if self._connection is None:
            return
        calls = [
            ("RegisterResourceEvent", GLib.Variant("(susu)", (self._app_id, 0, uri, _ACCESSED)))
        ]
        if content_type:
            calls.append(("RegisterResourceMimetype", GLib.Variant("(ss)", (uri, content_type))))
        for method, parameters in calls:
            self._connection.call(
                BUS_NAME,
                OBJECT_PATH,
                INTERFACE,
                method,
                parameters,
                None,
                # Not worth starting the service for
                Gio.DBusCallFlags.NO_AUTO_START,
                _CALL_TIMEOUT_MS,
                None,
                self._on_reply,
                method,
            )

    @staticmethod
    def _on_reply(connection, result, method):
        try:
            connection.call_finish(result)
        except GLib.Error as error:
            log.debug("Plasma did not take %s: %s", method, error.message)
