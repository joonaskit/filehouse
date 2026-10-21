"""The windows of the process, and whether it goes on without one: staying, the tray,
`--quit`, and a first window as a start."""

import pytest
from shiboken6 import isValid
from ui_helpers import TIMEOUT, gfile, off_the_ui_thread

from filehouse import application
from filehouse.application import Windows
from filehouse.core import locations
from filehouse.integration import portal
from filehouse.integration.portal import Request
from filehouse.ui import main_window, settings
from filehouse.ui.tray import Tray


class App:
    """The application, with what would end it only written down."""

    def __init__(self, real):
        self._real = real
        self.ends_with_last_window = None
        self.quits = 0

    def setQuitOnLastWindowClosed(self, on):
        self.ends_with_last_window = on

    def quit(self):
        self.quits += 1

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    return tmp_path


class Look:
    """What a start reads and applies, written down: the tests' own look is left alone."""

    def __init__(self):
        self.read = []

    def set_density(self, density):
        self.read.append(density)

    def apply(self, app, theme):
        self.read.append("theme")


@pytest.fixture
def look(monkeypatch):
    look = Look()
    monkeypatch.setattr(application, "style", look)
    return look


@pytest.fixture
def made(qtbot, qapp, jobs, home_dir, look):
    app, tray = App(qapp), Tray()
    windows = Windows(app, jobs, tray)
    windows.app, windows.tray = app, tray
    yield windows
    settings.changes.stay_running_changed.disconnect(windows._on_stay_running_changed)
    close_all(qtbot, windows)


def close_all(qtbot, windows):
    for item in windows.windows + windows.dialogs:
        if isValid(item):
            item.close()
    qtbot.waitUntil(lambda: not windows.windows and not windows.dialogs, timeout=TIMEOUT)


def target(path):
    return locations.StartTarget(gfile(path))


def paths(windows):
    return [window._location.get_path() for window in windows.windows]


def test_it_stays_and_is_seen_in_the_tray_as_long_as_that_is_the_setting(made):
    assert made.app.ends_with_last_window is False
    assert made.tray.shown

    settings.set_stay_running(False)
    assert made.app.ends_with_last_window is True
    assert not made.tray.shown

    settings.set_stay_running(True)
    assert made.app.ends_with_last_window is False
    assert made.tray.shown


def test_with_the_setting_off_it_starts_as_one_that_ends(qtbot, qapp, jobs, home_dir):
    settings.set_stay_running(False)
    app, tray = App(qapp), Tray()

    windows = Windows(app, jobs, tray)
    settings.changes.stay_running_changed.disconnect(windows._on_stay_running_changed)

    assert app.ends_with_last_window is True
    assert not tray.shown


def test_a_window_for_each_folder_named_and_one_for_one_named_twice(made, tree):
    made.open_all([target(tree / "one"), target(tree / "two"), target(tree / "one")], "")

    assert paths(made) == [str(tree / "one"), str(tree / "two")]


def test_what_is_named_is_looked_at_off_the_ui_thread(qtbot, made, tree, stuck):
    made.open_all([target(tree / "one")], "")
    resolve = stuck(locations, "resolve_start")

    made.open_paths([str(tree / "two"), str(tree / "missing")], "")
    # The window there is goes on meanwhile, and the process does not end in the wait
    assert paths(made) == [str(tree / "one")]
    assert not made.nothing_to_stay_for()

    resolve.release()
    qtbot.waitUntil(lambda: len(made.windows) == 3, timeout=TIMEOUT)
    assert off_the_ui_thread(resolve)
    assert paths(made)[1] == str(tree / "two")
    # What is not there opens home, and says so
    assert paths(made)[2] == locations.home().get_path()


def test_started_with_nothing_it_opens_home(made):
    made.open_all([], "")

    assert paths(made) == [locations.home().get_path()]


def test_closing_the_last_window_ends_nothing(qtbot, made, tree):
    made.open_all([target(tree / "one")], "")

    close_all(qtbot, made)

    assert made.app.quits == 0
    assert made.tray.shown
    # And the next one is opened as the first was
    made.open_all([target(tree / "two")], "")
    assert paths(made) == [str(tree / "two")]


def test_a_click_on_the_tray_opens_a_window_only_when_there_is_none(made):
    made.tray.open_requested.emit()
    assert paths(made) == [locations.home().get_path()]

    made.tray.open_requested.emit()

    assert len(made.windows) == 1


def test_new_window_in_the_trays_menu_opens_one_more(made, tree):
    made.open_all([target(tree / "one")], "")

    made.tray.new_window.trigger()

    assert paths(made) == [str(tree / "one"), locations.home().get_path()]


def test_a_first_window_is_a_start_and_reads_the_look_again(qtbot, made, tree, look):
    # Not the very first: the look was read when the process started
    made.open_all([target(tree / "one")], "")
    settings.set_density("compact")
    # And not while a window is open: it was made at the sizes of before
    made.open_all([target(tree / "two")], "")
    assert look.read == []

    close_all(qtbot, made)
    made.open_all([target(tree / "one")], "")

    assert look.read == ["compact", "theme"]
    made.open_all([target(tree / "two")], "")
    assert look.read == ["compact", "theme"]


def test_asked_to_quit_with_nothing_open_it_ends_at_once(made):
    made.quit_when_done()

    assert made.app.quits == 1
    assert made.app.ends_with_last_window is True
    assert not made.tray.shown


def test_asked_to_quit_with_a_window_open_it_ends_with_the_window(made, tree):
    made.open_all([target(tree / "one")], "")

    made.quit_when_done()

    # Qt ends the app with its last window from here on; nothing is closed for it
    assert made.app.quits == 0
    assert made.app.ends_with_last_window is True
    assert not made.tray.shown
    assert made.windows[0].isVisible()


def test_quit_in_the_trays_menu_closes_the_windows(qtbot, made, tree):
    made.open_all([target(tree / "one"), target(tree / "two")], "")

    made.tray.quit.trigger()

    assert made.app.ends_with_last_window is True
    assert not made.tray.shown
    assert not any(isValid(w) and w.isVisible() for w in made.windows)


class _Refusing(main_window.MainWindow):
    """A window with jobs running that was told "Don't close"."""

    refuses = True

    def closeEvent(self, event):
        if self.refuses:
            event.ignore()
        else:
            super().closeEvent(event)


def test_quit_in_the_tray_leaves_all_as_it_was_when_a_window_stays_open(made, tree, monkeypatch):
    monkeypatch.setattr(application, "MainWindow", _Refusing)
    made.open_all([target(tree / "one")], "")
    window = made.windows[0]

    made.tray.quit.trigger()

    assert window.isVisible()
    assert made.app.ends_with_last_window is False
    assert made.tray.shown
    assert made.app.quits == 0
    window.refuses = False


def test_a_process_started_for_a_call_may_end_only_with_nothing_open(qtbot, made, tree):
    assert made.nothing_to_stay_for()

    made.open_all([target(tree / "one")], "")
    assert not made.nothing_to_stay_for()

    close_all(qtbot, made)
    assert made.nothing_to_stay_for()


def test_a_dialog_for_another_app_is_kept_until_it_is_closed(qtbot, made, tree):
    answers = []
    request = Request(
        portal.OPEN, "org.example.App", "", "Pick", lambda *answer: answers.append(answer)
    )
    request.folder = gfile(tree)

    dialog = made.choose(request)

    assert made.dialogs == [dialog] and dialog.isVisible()
    # A process started for it has something to stay for
    assert not made.nothing_to_stay_for()

    dialog.close()
    qtbot.waitUntil(lambda: not made.dialogs, timeout=TIMEOUT)
    assert answers == [(portal.CANCELLED, {})]
    assert made.nothing_to_stay_for()
