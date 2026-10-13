"""The back and forward buttons of a mouse go through the folders that were open."""

import pytest
from PySide6.QtCore import QPoint, Qt
from ui_helpers import TIMEOUT, gfile

from filehouse.integration import style
from filehouse.ui import main_window
from filehouse.ui.main_window import MainWindow

BACK, FORWARD = Qt.MouseButton.BackButton, Qt.MouseButton.ForwardButton
WIDE = style.px(main_window.NARROW_BELOW) + 200


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "one" / "two").mkdir(parents=True)
    (tmp_path / "one" / "file.txt").write_text("")
    (tmp_path / "other").mkdir()
    return tmp_path


@pytest.fixture
def window(qtbot, tree, jobs):
    window = MainWindow(gfile(tree), jobs=jobs)
    qtbot.addWidget(window)
    window.resize(WIDE, 600)
    window.show()
    loaded(qtbot, window._pane)
    return window


def loaded(qtbot, pane):
    qtbot.waitUntil(lambda: not pane.model.loading, timeout=TIMEOUT)


def go(qtbot, window, folder):
    window.navigate(gfile(folder))
    loaded(qtbot, window._pane)


def path(pane):
    return pane.location.get_path()


def items(pane):
    return pane.view._items().viewport()


@pytest.mark.parametrize("mode", [main_window.DETAILS, main_window.ICONS])
def test_over_the_list_they_go_back_and_forward(qtbot, window, tree, mode):
    window.set_view_mode(mode)
    go(qtbot, window, tree / "one")
    go(qtbot, window, tree / "one" / "two")

    qtbot.mouseClick(items(window._pane), BACK)
    assert path(window._pane) == str(tree / "one")
    qtbot.mouseClick(items(window._pane), BACK)
    assert path(window._pane) == str(tree)
    # Nothing further back: nothing happens
    qtbot.mouseClick(items(window._pane), BACK)
    assert path(window._pane) == str(tree)

    qtbot.mouseClick(items(window._pane), FORWARD)
    assert path(window._pane) == str(tree / "one")


def test_a_press_over_a_file_does_not_select_it(qtbot, window, tree):
    go(qtbot, window, tree / "one")
    loaded(qtbot, window._pane)
    view = window._pane.view
    over_first = view._items().visualRect(view._items().model().index(0, 0)).center()

    qtbot.mouseClick(items(window._pane), FORWARD, pos=over_first)

    assert view.selected_count() == 0
    assert path(window._pane) == str(tree / "one")


def test_two_quick_presses_are_two_steps(qtbot, window, tree):
    go(qtbot, window, tree / "one")
    go(qtbot, window, tree / "one" / "two")

    # The second of two quick presses comes as a double click
    qtbot.mouseClick(items(window._pane), BACK)
    qtbot.mouseDClick(items(window._pane), BACK)

    assert path(window._pane) == str(tree)


def test_over_an_empty_folders_message_too(qtbot, window, tree):
    go(qtbot, window, tree / "other")
    assert not window._pane.view._items().isVisible()

    qtbot.mouseClick(window._pane.view, BACK)

    assert path(window._pane) == str(tree)


def test_in_a_split_they_are_for_the_list_under_the_pointer(qtbot, window, tree):
    go(qtbot, window, tree / "one")
    window.set_split(True)
    first, second = window._panes
    loaded(qtbot, second)
    window._activate(second)
    go(qtbot, window, tree / "other")
    assert window._pane is second

    qtbot.mouseClick(items(first), BACK)

    assert path(first) == str(tree)
    assert path(second) == str(tree / "other")
    assert window._pane is first


@pytest.mark.parametrize("where", ["sidebar", "status bar", "details"])
def test_elsewhere_in_the_window_they_are_for_the_active_list(qtbot, window, tree, where):
    go(qtbot, window, tree / "one")
    selected = window._sidebar.currentRow()
    target = {
        "sidebar": window._sidebar.viewport(),
        "status bar": window.statusBar(),
        "details": window._details,
    }[where]

    qtbot.mouseClick(target, BACK, pos=QPoint(20, 20))
    assert path(window._pane) == str(tree)
    qtbot.mouseClick(target, FORWARD, pos=QPoint(20, 20))

    assert path(window._pane) == str(tree / "one")
    # Not a click on the place under the pointer
    assert window._sidebar.currentRow() == selected
