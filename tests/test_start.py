"""A start that only hands its paths to the running process loads nothing of the
application: importing Qt and the windows is most of the time such a start would take."""

import subprocess
import sys

_HEAVY = ("PySide6", "shiboken6", "filehouse.application", "filehouse.ui", "filehouse.qt_models")

_SCRIPT = """
import sys
from filehouse import app

sent = []

class Taken:
    def claim(self):
        return False

    def send(self, uris, token=""):
        sent.append(uris)
        return True

app.Instance = Taken
assert app.main(["/tmp"]) == 0
assert sent == [["file:///tmp"]], sent
print(*sorted(name for name in sys.modules if name.startswith({heavy!r})))
"""


def test_a_start_that_hands_over_imports_no_qt_and_no_windows():
    done = subprocess.run(
        [sys.executable, "-c", _SCRIPT.format(heavy=_HEAVY)],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == []


_QUIT = """
import sys
from filehouse import app

asked = []

class Instance:
    def claim(self):
        return {claim}

    def ask_to_quit(self):
        asked.append("quit")
        return True

    def close(self):
        asked.append("close")

app.Instance = Instance
assert app.main(["--quit"]) == 0
print(*asked, *sorted(name for name in sys.modules if name.startswith({heavy!r})))
"""


def quit_with(claim):
    done = subprocess.run(
        [sys.executable, "-c", _QUIT.format(claim=claim, heavy=_HEAVY)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    return done.stdout.split()


def test_quit_asks_the_process_that_runs_and_starts_no_application():
    assert quit_with(False) == ["quit"]


def test_quit_with_none_running_gives_the_name_back_and_starts_no_application():
    assert quit_with(True) == ["close"]
    assert quit_with(None) == ["close"]
