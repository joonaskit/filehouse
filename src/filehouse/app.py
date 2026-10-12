"""Entry point: arguments, application setup and the first windows."""

import argparse
import gc
import os
import signal
import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from filehouse import APP_ID, __version__
from filehouse.core import locations
from filehouse.integration import style, theme
from filehouse.integration.instance import Instance, activation_token
from filehouse.integration.island import IslandClient, IslandReporter
from filehouse.integration.notifications import NotificationReporter, Notifier
from filehouse.integration.recent import RecentFiles
from filehouse.integration.theme_watcher import ThemeWatcher
from filehouse.qt_models.favourites import Favourites
from filehouse.qt_models.job_model import JobModel
from filehouse.ui import clipboard, settings
from filehouse.ui.dialogs.prompter import Prompter
from filehouse.ui.dialogs.properties import PropertiesDialog
from filehouse.ui.main_window import MainWindow

# How often the garbage is collected, in milliseconds
_COLLECT_MS = 5000
# How long a process started by the bus waits for the call it was started for
_SERVICE_WAIT_MS = 10000


def main(argv=None):
    parser = argparse.ArgumentParser(prog=APP_ID, description="A small file browser.")
    parser.add_argument("paths", nargs="*", metavar="PATH", help="folders, files or file:// URIs")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    # Started by the session bus for another app's "Show in folder": no window of its own
    parser.add_argument("--service", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    # One process for the session. When one runs already it opens the windows, and this
    # one is done: before anything of the application is set up
    instance = Instance()
    primary = instance.claim()
    if primary is False:
        # As URIs: a path is relative to where this process was started, not that one
        uris = [locations.uri(locations.from_argument(path)) for path in args.paths]
        if args.service or instance.send(uris, activation_token()):
            return 0
        # It does not answer: better a second process than no window

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
    # Windows, and the properties dialogs opened for other apps, are kept alive here
    # until they are closed
    windows = []
    dialogs = []

    # The island mirrors the jobs. With no island running this does nothing
    client = IslandClient()
    reporter = IslandReporter(jobs, client, jobs)

    def show_window_of(job):
        origin = jobs.origin(job)
        window = origin if origin in windows and isValid(origin) else next(iter(windows), None)
        if window is not None:
            window.come_forward()

    reporter.show_requested.connect(show_window_of)
    client.start()

    # A job that ends with no window to show it: a notification, unless the island does
    def window_visible():
        return any(isValid(w) and w.isVisible() and not w.isMinimized() for w in windows)

    # What is opened is told to the desktop's lists of recent files, unless the user
    # chose not to, here or in Plasma's settings
    recent = RecentFiles(settings.remember_opened)
    recent.start()
    # The folders the user keeps in the bar under the toolbar: Dolphin's list of places
    favourites = Favourites(parent=app)

    notifier = Notifier()
    NotificationReporter(
        jobs,
        notifier,
        window_visible,
        lambda: client.available,
        jobs,
        long_job_seconds=settings.long_job_seconds,
        enabled=settings.notify,
    )
    notifier.start()
    # The process may end the moment the last job has: what was sent must be on the bus
    app.aboutToQuit.connect(notifier.flush)

    def open_window(target, token=""):
        window = MainWindow(
            target.location,
            select=target.select,
            message=target.message,
            jobs=jobs,
            recent=recent,
            favourites=favourites,
        )
        windows.append(window)
        window.destroyed.connect(lambda _obj=None, w=window: windows.remove(w))
        _use_token(token)
        window.come_forward()
        return window

    def open_all(targets, token):
        seen = []
        for target in targets or [locations.StartTarget(locations.home())]:
            # One window for a folder, however often it was named
            key = (locations.uri(target.location), target.select)
            if key not in seen:
                seen.append(key)
                open_window(target, token)

    def show_properties(uris, token):
        if not uris:
            return
        dialog = PropertiesDialog([locations.from_uri(uri) for uri in uris], None)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialogs.append(dialog)
        dialog.destroyed.connect(lambda _obj=None, d=dialog: dialogs.remove(d))
        _use_token(token)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    # Asked by a later start of the app, or by another app for "Show in folder"
    instance.on_open = lambda uris, token: open_all(
        [locations.resolve_start(uri) for uri in uris], token
    )
    instance.on_show_folders = instance.on_open
    instance.on_show_items = lambda uris, token: open_all(
        [locations.resolve_item(uri) for uri in uris], token
    )
    instance.on_show_properties = show_properties

    if args.service:
        # Started for a call that is on its way. If nothing came of it, it is over
        QTimer.singleShot(
            _SERVICE_WAIT_MS,
            lambda: None if windows or dialogs or jobs.active_count() else app.quit(),
        )
    else:
        open_all([locations.resolve_start(path) for path in args.paths], "")
    return app.exec()


def _use_token(token):
    """Let the next window that is shown come to the front: the compositor allows it to
    whoever holds the token the launcher or the asking app was given."""
    if token:
        os.environ["XDG_ACTIVATION_TOKEN"] = token


if __name__ == "__main__":
    sys.exit(main())
