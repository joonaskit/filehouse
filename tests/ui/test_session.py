"""The tabs and the split of last time: kept when the last window closes, opened again
by a start without a path."""

import shutil

import pytest
from shiboken6 import isValid
from ui_helpers import TIMEOUT, gfile, off_the_ui_thread

from filehouse.core import locations
from filehouse.integration import style
from filehouse.ui import main_window, settings
from filehouse.ui.main_window import MainWindow, session_to_open

WIDE = style.px(main_window.NARROW_BELOW) + 200
NARROW = style.px(main_window.NARROW_BELOW) - 200


@pytest.fixture
def tree(tmp_path):
    for name in ("one", "two", "three"):
        (tmp_path / name / "sub").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def open_window(qtbot, jobs):
    windows = []

    def open_at(path=None, session=None, width=WIDE):
        settings.set_window_size(main_window.QSize(width, 600))
        location = gfile(path) if path else session[0][0][0][0]
        window = MainWindow(location, jobs=jobs, session=session)
        windows.append(window)
        window.show()
        for pane in window._all_panes():
            qtbot.waitUntil(lambda pane=pane: not pane.model.loading, timeout=TIMEOUT)
        return window

    yield open_at
    for window in windows:
        if isValid(window):
            window.close()


def uri(path):
    return gfile(path).get_uri()


def folders(window):
    """The folders of every tab, the active pane's marked with a star."""
    return [
        [
            pane.location.get_basename() + ("*" if pane is tab.active and tab.is_split else "")
            for pane in tab.panes
        ]
        for tab in window._tabs
    ]


def test_closing_the_last_window_keeps_its_tabs(qtbot, tree, open_window):
    window = open_window(tree / "one")
    window.new_tab(gfile(tree / "two"))
    window.set_split(True, remember=False, location=gfile(tree / "three"))
    window.new_tab(gfile(tree / "three"), activate=False)

    window.close()

    assert settings.session() == (
        [
            ([uri(tree / "one")], 0),
            ([uri(tree / "two"), uri(tree / "three")], 0),
            ([uri(tree / "three")], 0),
        ],
        1,
    )


def test_they_are_opened_again(qtbot, tree, open_window):
    settings.set_session(
        [
            ([uri(tree / "one")], 0),
            ([uri(tree / "two"), uri(tree / "three" / "sub")], 1),
            ([uri(tree / "three")], 0),
        ],
        1,
    )

    window = open_window(session=session_to_open())

    assert folders(window) == [["one"], ["two", "sub*"], ["three"]]
    assert window._tab is window._tabs[1]
    assert window._tab_bar.currentIndex() == 1 and window._tab_bar.isVisible()
    assert window.is_split and window._actions.split_view.isChecked()
    assert window.windowTitle() == "sub — Files"
    # A pane that was opened again starts its own history
    assert not window._other_pane().history.can_go_back
    assert not window._pane.history.can_go_back


def test_what_comes_back_is_what_was_closed(qtbot, tree, open_window):
    first = open_window(tree / "one")
    first.new_tab(gfile(tree / "two"))
    first.set_split(True, remember=False)
    first._other_pane().navigate(gfile(tree / "three"))
    first.close()

    second = open_window(session=session_to_open())

    assert folders(second) == [["one"], ["two*", "three"]]
    assert second._tab is second._tabs[1]


def test_a_window_that_is_not_the_last_changes_nothing(qtbot, tree, open_window):
    staying = open_window(tree / "one")
    going = open_window(tree / "two")
    settings.set_session([([uri(tree / "three")], 0)], 0)

    going.close()
    assert settings.session() == ([([uri(tree / "three")], 0)], 0)

    staying.close()
    assert settings.session() == ([([uri(tree / "one")], 0)], 0)


def test_nothing_is_looked_at_before_the_window_opens(tree, monkeypatch):
    def looked(*_args):
        raise AssertionError("a folder of last time was looked at")

    monkeypatch.setattr(locations, "is_folder", looked)
    monkeypatch.setattr(locations, "nearest_folder", looked)
    settings.set_session([([uri(tree / "one")], 0), ([uri(tree / "gone")], 0)], 1)

    tabs, front = session_to_open()

    assert [[folder.get_basename() for folder in folders] for folders, _active in tabs] == [
        ["one"],
        ["gone"],
    ]
    assert front == 1


def test_a_folder_that_is_gone_is_the_nearest_one_above_or_left_out(qtbot, tree, open_window):
    settings.set_session(
        [
            ([uri(tree / "one" / "sub" / "deeper")], 0),
            (["no-such-scheme://host/share"], 0),
            ([uri(tree / "two"), "no-such-scheme://host/other"], 1),
            ([uri(tree / "three")], 0),
        ],
        3,
    )
    shutil.rmtree(tree / "one" / "sub")

    window = open_window(session=session_to_open())

    # Found once each could not be listed: the window opened without waiting for them
    qtbot.waitUntil(lambda: folders(window) == [["one"], ["two"], ["three"]], timeout=TIMEOUT)
    # The pane that was active is gone with its folder; the tab in front is the one it was
    assert not window._tabs[1].is_split
    assert window._tab is window._tabs[2]
    # As if it had been opened there
    assert not window._tabs[0].active.history.can_go_back


def test_a_pane_that_moved_on_is_not_taken_back_to_a_folder_of_last_time(
    qtbot, tree, open_window, stuck
):
    nearest = stuck(locations, "nearest_folder")
    settings.set_session([([uri(tree / "two" / "gone")], 0)], 0)
    window = open_window(session=session_to_open())
    pane = window._pane
    # The folder could not be listed, and where to go instead does not come
    qtbot.waitUntil(lambda: bool(nearest.threads), timeout=TIMEOUT)

    window.navigate(gfile(tree / "three"))
    nearest.release()
    qtbot.waitUntil(lambda: not pane._background.busy, timeout=TIMEOUT)

    assert pane.location.get_path() == str(tree / "three")
    assert off_the_ui_thread(nearest)


def test_with_nothing_left_of_last_time_the_window_goes_home(qtbot, open_window):
    settings.set_session([(["no-such-scheme://host/share"], 0)], 0)

    window = open_window(session=session_to_open())

    qtbot.waitUntil(
        lambda: locations.same(window._pane.location, locations.home()), timeout=TIMEOUT
    )
    assert len(window._tabs) == 1


def test_nothing_to_open(tree):
    assert session_to_open() is None

    settings.set_session([([uri(tree / "one")], 0)], 0)
    settings.set_restore_tabs(False)
    assert session_to_open() is None

    settings._store().setValue("window/tabs", "{not json")
    settings.set_restore_tabs(True)
    assert session_to_open() is None


def test_a_narrow_window_opens_the_active_pane_of_a_split(qtbot, tree, open_window):
    settings.set_session(
        [([uri(tree / "one"), uri(tree / "two")], 1), ([uri(tree / "three")], 0)], 0
    )

    window = open_window(session=session_to_open(), width=NARROW)

    assert folders(window) == [["two"], ["three"]]
    assert not window.is_split


def test_the_split_of_last_time_and_not_the_one_for_new_windows(qtbot, tree, open_window):
    settings.set_split(True)
    settings.set_session([([uri(tree / "one")], 0)], 0)

    window = open_window(session=session_to_open())

    assert folders(window) == [["one"]]
