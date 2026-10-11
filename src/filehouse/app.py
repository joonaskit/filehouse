"""Entry point: arguments, application setup and the first windows."""

import argparse
import signal
import sys

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from filehouse import APP_ID, __version__
from filehouse.core import locations
from filehouse.integration import style, theme
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
