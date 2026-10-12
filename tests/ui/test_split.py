"""Two folders side by side: what each pane keeps to itself and what follows the active one."""

import os

import pytest
from PySide6.QtCore import QPoint, Qt
from ui_helpers import TIMEOUT, gfile

from filehouse.core.operations import JobKind
from filehouse.integration import style
from filehouse.ui import main_window, settings
from filehouse.ui.main_window import MainWindow

WIDE = style.px(main_window.NARROW_BELOW) + 200
NARROW = style.px(main_window.NARROW_BELOW) - 200


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "left").mkdir()
    (tmp_path / "right").mkdir()
    (tmp_path / "left" / "a.txt").write_text("aaa")
    (tmp_path / "left" / "b.txt").write_text("bbb")
    (tmp_path / "left" / ".hidden").write_text("")
    return tmp_path


@pytest.fixture
def window(qtbot, tree, jobs):
    window = MainWindow(gfile(tree / "left"), jobs=jobs)
    qtbot.addWidget(window)
    window.resize(WIDE, 600)
    window.show()
    wait_loaded(qtbot, window._pane)
    return window


def wait_loaded(qtbot, pane):
    qtbot.waitUntil(lambda: not pane.model.loading, timeout=TIMEOUT)


def split(qtbot, window):
    window.set_split(True)
    first, second = window._panes
    wait_loaded(qtbot, second)
    return first, second


def path(pane):
    return pane.location.get_path()


def test_it_starts_with_one_pane(window):
    assert not window.is_split
    assert not window._split_view.isChecked()
    assert window._details.isVisible()


def test_the_toggle_opens_a_second_pane_in_the_same_folder(qtbot, window, tree):
    first, second = split(qtbot, window)

    assert window.is_split
    assert window._split_view.isChecked()
    assert path(second) == str(tree / "left")
    # The active one stays, and there is no room for the details
    assert window._pane is first
    assert not window._details.isVisible()
    assert first.view.isVisible() and second.view.isVisible()


def test_the_key_toggles_it(qtbot, window):
    qtbot.keyClick(window, Qt.Key.Key_F3)
    assert window.is_split

    qtbot.keyClick(window, Qt.Key.Key_F3)
    assert not window.is_split
    assert not window._split_view.isChecked()


def test_each_pane_has_its_own_folder_history_and_selection(qtbot, tree, window):
    first, second = split(qtbot, window)
    second.view.focused.emit()
    window.navigate(gfile(tree / "right"))

    assert window._pane is second
    assert path(second) == str(tree / "right")
    assert path(first) == str(tree / "left")
    assert second.history.can_go_back
    assert not first.history.can_go_back

    first.view.select_name("a.txt")
    assert first.view.selected_count() == 1
    assert second.view.selected_count() == 0


def test_the_window_follows_the_active_pane(qtbot, tree, window):
    first, second = split(qtbot, window)
    second.view.focused.emit()
    window.navigate(gfile(tree / "right"))
    wait_loaded(qtbot, second)

    assert window.windowTitle() == "right — Files"
    assert window._back.isEnabled()
    assert window._count_label.text() == "0 items"

    first.view.focused.emit()

    assert window._pane is first
    assert window.windowTitle() == "left — Files"
    assert not window._back.isEnabled()
    assert window._count_label.text() == "2 items"


def test_closing_the_split_keeps_the_active_pane(qtbot, tree, window):
    first, second = split(qtbot, window)
    second.view.focused.emit()
    window.navigate(gfile(tree / "right"))

    window.set_split(False)

    assert window._panes == [second]
    assert window._pane is second
    assert path(second) == str(tree / "right")
    assert window._details.isVisible()
    assert window.windowTitle() == "right — Files"


def test_only_the_active_pane_is_marked(qtbot, window):
    first, second = split(qtbot, window)

    assert first.view.property("active") and not second.view.property("active")
    assert first.view.property("split") and second.view.property("split")

    second.view.focused.emit()

    assert second.view.property("active") and not first.view.property("active")


def test_view_mode_and_hidden_files_apply_to_both(qtbot, window):
    first, second = split(qtbot, window)

    window.set_view_mode(settings.ICONS)
    window._hidden.trigger()

    assert first.view.mode == second.view.mode == settings.ICONS
    assert first.proxy.show_hidden and second.proxy.show_hidden
    # A pane opened later starts like the others
    window.set_split(False)
    window.set_split(True)
    assert window._panes[1].view.mode == settings.ICONS


def test_the_sort_order_is_the_panes_own(qtbot, window):
    first, second = split(qtbot, window)

    first.view.sort(main_window.SIZE, Qt.SortOrder.DescendingOrder)

    assert second.view.sort_column() == main_window.NAME


def test_copy_to_the_other_pane(qtbot, tree, jobs, wait_final, window):
    first, second = split(qtbot, window)
    second.view.focused.emit()
    window.navigate(gfile(tree / "right"))
    first.view.focused.emit()
    first.view.select_name("a.txt")

    assert window._copy_other.isEnabled()
    job = wait_final(jobs, window.copy_to_other_pane())

    assert job.kind is JobKind.COPY
    assert sorted(os.listdir(tree / "left")) == [".hidden", "a.txt", "b.txt"]
    assert os.listdir(tree / "right") == ["a.txt"]


def test_move_to_the_other_pane(qtbot, tree, jobs, wait_final, window):
    first, second = split(qtbot, window)
    second.view.focused.emit()
    window.navigate(gfile(tree / "right"))
    first.view.focused.emit()
    first.view.select_name("a.txt")

    job = wait_final(jobs, window.move_to_other_pane())

    assert job.kind is JobKind.MOVE
    assert os.listdir(tree / "right") == ["a.txt"]
    assert sorted(os.listdir(tree / "left")) == [".hidden", "b.txt"]


def test_nothing_to_move_to_the_folder_it_is_in(qtbot, jobs, window):
    first, second = split(qtbot, window)
    first.view.select_name("a.txt")

    # Both panes show the same folder
    assert window.move_to_other_pane() is None
    assert jobs.jobs == []


def test_the_actions_need_a_split_and_a_selection(qtbot, window):
    window._pane.view.select_name("a.txt")
    assert not window._copy_other.isEnabled()
    assert not window._move_other.isEnabled()
    assert window.copy_to_other_pane() is None

    window.set_split(True)
    assert window._copy_other.isEnabled()
    assert window._move_other.isEnabled()

    window._pane.view.selection_model().clearSelection()
    assert not window._copy_other.isEnabled()


def test_a_drop_goes_to_the_pane_it_lands_on(qtbot, tree, jobs, wait_final, window):
    first, second = split(qtbot, window)
    window._pane = first
    second.navigate(gfile(tree / "right"))
    wait_loaded(qtbot, second)

    # As the view reports it: URIs, no folder, no keys, a position
    uris = [gfile(tree / "left" / "a.txt").get_uri()]
    second.view.dropped.emit(uris, None, Qt.KeyboardModifier.NoModifier, QPoint(0, 0))
    job = jobs.jobs[0]
    wait_final(jobs, job)

    assert window._pane is second
    assert job.kind is JobKind.MOVE
    assert os.listdir(tree / "right") == ["a.txt"]


def test_the_split_is_not_for_a_narrow_window(qtbot, window):
    window.set_split(True)

    window.resize(NARROW, 600)
    qtbot.waitUntil(lambda: window.is_narrow, timeout=TIMEOUT)

    assert not window.is_split
    assert not window._split_view.isEnabled()
    window.set_split(True)
    assert not window.is_split
    assert not window._split_view.isChecked()

    window.resize(WIDE, 600)
    qtbot.waitUntil(lambda: not window.is_narrow, timeout=TIMEOUT)
    assert window._split_view.isEnabled()
    assert window._details.isVisible()


def test_a_drive_that_goes_away_leaves_neither_pane_on_it(qtbot, tree, window):
    first, second = split(qtbot, window)

    window._on_volume_removed(gfile(tree))

    qtbot.waitUntil(lambda: path(first) == path(second) != str(tree / "left"), timeout=TIMEOUT)


def test_the_split_is_remembered_for_the_next_window(qtbot, tree, jobs):
    first = MainWindow(gfile(tree / "left"), jobs=jobs)
    qtbot.addWidget(first)
    first.resize(WIDE, 600)
    first.show()
    first.set_split(True)
    assert settings.split()

    second = MainWindow(gfile(tree / "right"), jobs=jobs)
    qtbot.addWidget(second)
    second.resize(WIDE, 600)
    second.show()

    assert second.is_split
    assert second._split_view.isChecked()
    assert [path(pane) for pane in second._panes] == [str(tree / "right")] * 2

    second.set_split(False)
    assert not settings.split()


def test_a_narrow_window_neither_splits_nor_forgets_the_split(qtbot, window):
    window.set_split(True)

    window.resize(NARROW, 600)
    qtbot.waitUntil(lambda: window.is_narrow, timeout=TIMEOUT)

    assert not window.is_split
    # Not the user's choice: the next wide window is split again
    assert settings.split()


# Open a folder of the list in the other pane


def folder_info(window, name):
    return window._model.info(window._model.row_for_name(name))


def test_a_folder_opens_in_split_view_from_its_menu_and_a_file_does_not(qtbot, tree, window):
    (tree / "left" / "sub").mkdir()
    qtbot.waitUntil(lambda: window._model.row_for_name("sub") >= 0, timeout=TIMEOUT)

    folder = [a.text() for a in window._menu_for(folder_info(window, "sub")).actions()]
    file = [a.text() for a in window._menu_for(folder_info(window, "a.txt")).actions()]

    # Right below Open in new tab
    assert folder[folder.index("Open in new tab") + 1] == "Open in split view"
    assert "Open in split view" not in file


def test_it_splits_the_window_and_shows_the_folder_in_the_new_pane(qtbot, tree, window):
    (tree / "left" / "sub").mkdir()
    (tree / "left" / "sub" / "inner.txt").write_text("x")
    qtbot.waitUntil(lambda: window._model.row_for_name("sub") >= 0, timeout=TIMEOUT)
    first = window._pane
    assert not window.is_split

    window._open_in_split(folder_info(window, "sub"))

    assert window.is_split
    # The list the folder was chosen in stays where it was, and stays the active one
    assert window._pane is first
    assert first.location.get_path() == str(tree / "left")
    other = window._other_pane()
    assert other.location.get_path() == str(tree / "left" / "sub")
    wait_loaded(qtbot, other)
    assert [other.model.info(0).name] == ["inner.txt"]
    # Not a choice of how the next window opens
    assert not settings.split()


def test_a_window_that_is_split_already_opens_it_in_the_other_pane(qtbot, tree, window):
    (tree / "left" / "sub").mkdir()
    qtbot.waitUntil(lambda: window._model.row_for_name("sub") >= 0, timeout=TIMEOUT)
    window.set_split(True)
    other = window._other_pane()
    window._open_in_split(folder_info(window, "sub"))

    assert window._other_pane() is other
    assert other.location.get_path() == str(tree / "left" / "sub")
    assert len(window._tab.panes) == 2
    # Back in that pane goes to where it was
    other.go_back()
    assert other.location.get_path() == str(tree / "left")


def test_the_other_pane_can_be_the_one_it_is_opened_from(qtbot, tree, window):
    (tree / "left" / "sub").mkdir()
    qtbot.waitUntil(lambda: window._model.row_for_name("sub") >= 0, timeout=TIMEOUT)
    window.set_split(True)
    right = window._other_pane()
    window._activate(right)
    wait_loaded(qtbot, right)
    qtbot.waitUntil(lambda: right.model.row_for_name("sub") >= 0, timeout=TIMEOUT)
    left = window._other_pane()

    window._open_in_split(right.model.info(right.model.row_for_name("sub")))

    assert window._pane is right
    assert left.location.get_path() == str(tree / "left" / "sub")


def test_a_narrow_window_has_no_split_to_open_it_in(qtbot, tree, window):
    (tree / "left" / "sub").mkdir()
    qtbot.waitUntil(lambda: window._model.row_for_name("sub") >= 0, timeout=TIMEOUT)
    window.resize(NARROW, 600)

    actions = [a.text() for a in window._menu_for(folder_info(window, "sub")).actions()]
    window._open_in_split(folder_info(window, "sub"))

    assert "Open in split view" not in actions
    assert not window.is_split
