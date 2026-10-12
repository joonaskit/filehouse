"""Entry point: arguments, application setup and the first windows."""

import argparse
import signal
import sys

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from filehouse import APP_ID, __version__
from filehouse.core import locations
from filehouse.integration import style, theme
from filehouse.integration.island import IslandClient, IslandReporter
from filehouse.integration.notifications import NotificationReporter, Notifier
from filehouse.qt_models.job_model import JobModel
from filehouse.ui import clipboard
from filehouse.ui.dialogs.prompter import Prompter
from filehouse.ui.main_window import MainWindow


def main(argv=None):
    parser = argparse.ArgumentParser(prog=APP_ID, description="A small file browser.")
    parser.add_argument("paths", nargs="*", metavar="PATH", help="folders, files or file:// URIs")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_ID)
    # On Wayland this is the app id the shell's dock matches against StartupWMClass
    QGuiApplication.setDesktopFileName(APP_ID)
    # Let Ctrl+C in a terminal end the app
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    style.apply(app, theme.load_theme())

    targets = [locations.resolve_start(path) for path in args.paths]
    if not targets:
        targets = [locations.StartTarget(locations.home())]
    # One set of jobs for the process: every window shows them, and so will the island
    jobs = JobModel(app)
    Prompter(jobs, jobs)
    app.aboutToQuit.connect(clipboard.release)
    # Every launch is its own process; windows are kept alive here until closed
    windows = []

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

    notifier = Notifier()
    NotificationReporter(jobs, notifier, window_visible, lambda: client.available, jobs)
    notifier.start()
    # The process may end the moment the last job has: what was sent must be on the bus
    app.aboutToQuit.connect(notifier.flush)
    for target in targets:
        window = MainWindow(
            target.location, select=target.select, message=target.message, jobs=jobs
        )
        windows.append(window)
        window.destroyed.connect(lambda _obj=None, w=window: windows.remove(w))
        window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
