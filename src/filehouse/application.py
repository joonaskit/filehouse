"""The application once it is known that this process is to be one: the setup, the
windows and what other processes ask of it. Everything heavy is imported here, and
`app.py` imports this only after a running process has not taken the start over."""

import gc
import os
import signal
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from filehouse import APP_ID
from filehouse.core import locations
from filehouse.integration import style, theme
from filehouse.integration.island import IslandClient, IslandReporter
from filehouse.integration.notifications import NotificationReporter, Notifier
from filehouse.integration.recent import RecentFiles
from filehouse.integration.theme_watcher import ThemeWatcher
from filehouse.qt_models.favourites import Favourites
from filehouse.qt_models.job_model import JobModel
from filehouse.ui import clipboard, main_window, settings
from filehouse.ui.dialogs.prompter import Prompter
from filehouse.ui.dialogs.properties import PropertiesDialog
from filehouse.ui.main_window import MainWindow, session_to_open
from filehouse.ui.tray import Tray

# How often the garbage is collected, in milliseconds
_COLLECT_MS = 5000
# How long a process started by the bus waits for the call it was started for
_SERVICE_WAIT_MS = 10000


class Windows:
    """The windows of the process and the properties dialogs opened for other apps,
    kept alive here until they are closed, and with them whether the process goes on:
    it stays without a window when that is the setting, seen in the tray."""

    def __init__(self, app, jobs, tray, recent=None, favourites=None):
        self._app = app
        self._jobs = jobs
        self._tray = tray
        self._recent = recent
        self._favourites = favourites
        self.windows = []
        self.dialogs = []
        # Whether a window has been closed since the look was read: see _start_over
        self._stale = False
        # With the last window closed the process stays, when that is the setting, and
        # the next start is only a call to it. Whether Qt ends the app with its last
        # window is the one place this is kept: `main_window.quit_app` asks it too
        app.setQuitOnLastWindowClosed(not settings.stay_running())
        settings.changes.stay_running_changed.connect(self._on_stay_running_changed)
        # Seen in the tray for as long as the process is one that stays: there it is
        # opened from and ended, with a window or without
        tray.open_requested.connect(self.show_a_window)
        tray.new_window_requested.connect(
            lambda: self.open_window(locations.StartTarget(locations.home()))
        )
        tray.quit_requested.connect(lambda: self.quit_when_done(close=True))
        tray.set_shown(settings.stay_running())

    def _on_stay_running_changed(self, on):
        self._app.setQuitOnLastWindowClosed(not on)
        self._tray.set_shown(on)

    def _open(self):
        return [item for item in self.windows + self.dialogs if isValid(item)]

    def any_visible(self):
        """Whether a window shows a job: not hidden, and not minimized."""
        return any(isValid(w) and w.isVisible() and not w.isMinimized() for w in self.windows)

    def show_window_of(self, job):
        origin = self._jobs.origin(job)
        known = origin in self.windows and isValid(origin)
        window = origin if known else next(iter(self.windows), None)
        if window is not None:
            window.come_forward()

    def show_a_window(self):
        """A click on the tray's icon: the window there is, or one as a start would."""
        shown = [w for w in self.windows if isValid(w)]
        if shown:
            shown[-1].come_forward()
        else:
            self.open_all([], "")

    def open_window(self, target, token="", session=None):
        self._start_over()
        window = MainWindow(
            target.location,
            select=target.select,
            message=target.message,
            jobs=self._jobs,
            recent=self._recent,
            favourites=self._favourites,
            session=session,
        )
        self.windows.append(window)
        window.destroyed.connect(lambda _obj=None, w=window: self._closed(self.windows, w))
        _use_token(token)
        window.come_forward()
        return window

    def open_all(self, targets, token):
        if not targets and not self.windows:
            # Started without a path, with no window open: where the last one was
            session = session_to_open()
            if session is not None:
                self.open_window(locations.StartTarget(session[0][0][0][0]), token, session)
                return
        seen = []
        for target in targets or [locations.StartTarget(locations.home())]:
            # One window for a folder, however often it was named
            key = (locations.uri(target.location), target.select)
            if key not in seen:
                seen.append(key)
                self.open_window(target, token)

    def show_properties(self, uris, token):
        if not uris:
            return
        self._start_over()
        dialog = PropertiesDialog([locations.from_uri(uri) for uri in uris], None)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.dialogs.append(dialog)
        dialog.destroyed.connect(lambda _obj=None, d=dialog: self._closed(self.dialogs, d))
        _use_token(token)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _closed(self, kept, item):
        kept.remove(item)
        self._stale = True

    def _start_over(self):
        """With nothing open, the next window is a start: what applies "at the next
        start" (the density, the scale and the fonts of the theme) is read again."""
        if self._stale and not self.windows and not self.dialogs:
            self._stale = False
            style.set_density(settings.density())
            style.apply(self._app, theme.load_theme())

    def quit_when_done(self, close=False):
        """`filehouse --quit`: not to stay any more. At once with nothing open, and
        otherwise as it was before there was staying: with the last window, or with the
        last job of a window that was closed over it. Quit in the tray's menu closes
        the windows as well (`close`)."""
        self._app.setQuitOnLastWindowClosed(True)
        if close:
            for item in self._open():
                if item.isVisible():
                    item.close()
            if any(item.isVisible() for item in self._open()):
                # A window with jobs running asked, and was told not to close: as it was
                self._app.setQuitOnLastWindowClosed(not settings.stay_running())
                return
        self._tray.set_shown(False)
        if not self._open() and not self._jobs.active_count():
            self._app.quit()

    def nothing_to_stay_for(self):
        """Whether a process started for a call that never came may end."""
        return not (self.windows or self.dialogs or self._jobs.active_count())


def run(args, instance):
    """Be the application: `args` as `app.py` parsed them, `instance` after its claim."""
    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_ID)
    # On Wayland this is the app id the shell's dock matches against StartupWMClass
    QGuiApplication.setDesktopFileName(APP_ID)
    # Python's garbage collector runs in whichever thread allocates enough, and a Qt
    # object whose wrapper it collects in a worker thread (a listing, a thumbnail)
    # aborts the process. It is for the main thread: off, and run from here
    gc.disable()
    collector = QTimer(app)
    collector.setInterval(_COLLECT_MS)
    collector.timeout.connect(gc.collect)
    collector.start()
    # Let Ctrl+C in a terminal end the app
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    style.set_density(settings.density())
    style.apply(app, theme.load_theme())
    # The shell's accent colour or corner roundness changed: follow it without a restart
    ThemeWatcher(app, parent=app)

    # One set of jobs for the process: every window shows them, and so will the island
    jobs = JobModel(app)
    Prompter(jobs, jobs)
    app.aboutToQuit.connect(clipboard.release)
    app.aboutToQuit.connect(instance.close)

    # What is opened is told to the desktop's lists of recent files, unless the user
    # chose not to, here or in Plasma's settings
    recent = RecentFiles(settings.remember_opened)
    recent.start()
    # The folders the user keeps in the bar under the toolbar: Dolphin's list of places
    favourites = Favourites(parent=app)
    windows = Windows(app, jobs, Tray(app), recent, favourites)

    # The island mirrors the jobs. With no island running this does nothing
    client = IslandClient()
    reporter = IslandReporter(jobs, client, jobs)
    reporter.show_requested.connect(windows.show_window_of)
    client.start()

    # A job that ends with no window to show it: a notification, unless the island does
    notifier = Notifier()
    NotificationReporter(
        jobs,
        notifier,
        windows.any_visible,
        lambda: client.available,
        jobs,
        long_job_seconds=settings.long_job_seconds,
        enabled=settings.notify,
    )
    notifier.start()
    # The process may end the moment the last job has: what was sent must be on the bus
    app.aboutToQuit.connect(notifier.flush)

    # Asked by a later start of the app, or by another app for "Show in folder"
    instance.on_open = lambda uris, token: windows.open_all(
        [locations.resolve_start(uri) for uri in uris], token
    )
    instance.on_show_folders = instance.on_open
    instance.on_show_items = lambda uris, token: windows.open_all(
        [locations.resolve_item(uri) for uri in uris], token
    )
    instance.on_show_properties = windows.show_properties
    instance.on_quit = windows.quit_when_done

    if args.service:
        # Started for a call that is on its way. If nothing came of it, it is over,
        # unless the process is one that stays
        QTimer.singleShot(
            _SERVICE_WAIT_MS,
            lambda: main_window.quit_app() if windows.nothing_to_stay_for() else None,
        )
    else:
        windows.open_all([locations.resolve_start(path) for path in args.paths], "")
    return app.exec()


def _use_token(token):
    """Let the next window that is shown come to the front: the compositor allows it to
    whoever holds the token the launcher or the asking app was given."""
    if token:
        os.environ["XDG_ACTIVATION_TOKEN"] = token
