"""Several folders in one window: what each tab keeps and what follows the shown one."""

import os

import pytest
from PySide6.QtCore import QPoint, Qt
from ui_helpers import TIMEOUT, gfile, settle

from filehouse.core.operations import JobKind
from filehouse.integration import style
from filehouse.ui import main_window, settings
from filehouse.ui.main_window import MainWindow

WIDE = style.px(main_window.NARROW_BELOW) + 200
NARROW = style.px(main_window.NARROW_BELOW) - 200


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "one" / "sub").mkdir(parents=True)
    (tmp_path / "two").mkdir()
    (tmp_path / "one" / "a.txt").write_text("aaa")
    return tmp_path


@pytest.fixture
def window(qtbot, tree, jobs):
    window = MainWindow(gfile(tree / "one"), jobs=jobs)
    qtbot.addWidget(window)
    window.resize(WIDE, 600)
    window.show()
    wait_loaded(qtbot, window._pane)
    return window


def wait_loaded(qtbot, pane):
    qtbot.waitUntil(lambda: not pane.model.loading, timeout=TIMEOUT)


def open_tab(qtbot, window, folder=None, **kwargs):
    tab = window.new_tab(gfile(folder) if folder else None, **kwargs)
    wait_loaded(qtbot, tab.active)
    return tab


def titles(window):
    bar = window._tab_bar
    return [bar.tabText(index) for index in range(bar.count())]


def path(pane):
    return pane.location.get_path()


def test_it_starts_with_one_tab_and_no_bar(window):
    assert len(window._tabs) == 1
    assert not window._tab_bar.isVisible()


def test_a_new_tab_opens_the_same_folder_and_shows_the_bar(qtbot, tree, window):
    first = window._tab
    tab = open_tab(qtbot, window)

    assert window._tab is tab
    assert path(tab.active) == str(tree / "one")
    assert window._tab_bar.isVisible()
    assert titles(window) == ["one", "one"]
    assert window._stack.currentWidget() is tab.splitter
    assert first is not tab


def test_each_tab_has_its_own_folder_and_history(qtbot, tree, window):
    first = window._tab
    open_tab(qtbot, window)
    window.navigate(gfile(tree / "two"))
    wait_loaded(qtbot, window._pane)

    assert window.windowTitle() == "two — Files"
    assert window._actions.back.isEnabled()
    assert titles(window) == ["one", "two"]

    window._tab_bar.setCurrentIndex(0)

    assert window._tab is first
    assert window.windowTitle() == "one — Files"
    assert not window._actions.back.isEnabled()
    assert window._count_label.text() == "2 items"


def test_the_keys_open_close_and_step_through_tabs(qtbot, window):
    qtbot.keyClick(window, Qt.Key.Key_T, Qt.KeyboardModifier.ControlModifier)
    assert len(window._tabs) == 2
    assert window._tab_bar.currentIndex() == 1

    qtbot.keyClick(window, Qt.Key.Key_PageUp, Qt.KeyboardModifier.ControlModifier)
    assert window._tab_bar.currentIndex() == 0
    qtbot.keyClick(window, Qt.Key.Key_PageDown, Qt.KeyboardModifier.ControlModifier)
    assert window._tab_bar.currentIndex() == 1
    # Round the end
    qtbot.keyClick(window, Qt.Key.Key_PageDown, Qt.KeyboardModifier.ControlModifier)
    assert window._tab_bar.currentIndex() == 0

    qtbot.keyClick(window, Qt.Key.Key_W, Qt.KeyboardModifier.ControlModifier)
    assert len(window._tabs) == 1
    assert not window._tab_bar.isVisible()
    assert window.isVisible()


def test_closing_the_last_tab_closes_the_window(qtbot, tree, jobs):
    # Not given to qtbot: the window deletes itself, and qtbot would close it again
    window = MainWindow(gfile(tree / "one"), jobs=jobs)
    window.show()
    wait_loaded(qtbot, window._pane)

    window.close_current()

    assert not window.isVisible()


def test_closing_a_tab_shows_a_neighbour(qtbot, tree, window):
    first = window._tab
    open_tab(qtbot, window, tree / "two")

    window.close_tab(1)

    assert window._tabs == [first]
    assert window._tab is first
    assert window.windowTitle() == "one — Files"
    assert window._stack.count() == 1


def test_closing_a_tab_behind_leaves_the_shown_one(qtbot, tree, window):
    open_tab(qtbot, window, tree / "two")
    shown = window._tab

    window.close_tab(0)

    assert window._tab is shown
    assert window._tabs == [shown]
    assert window.windowTitle() == "two — Files"


def test_a_middle_click_opens_a_folder_in_a_tab_behind(qtbot, tree, window):
    first = window._tab
    info = window._model.info(window._model.row_for_name("sub"))

    window._view.open_in_tab.emit(info)

    assert window._tab is first
    assert len(window._tabs) == 2
    assert titles(window) == ["one", "sub"]
    assert path(window._tabs[1].active) == str(tree / "one" / "sub")


def test_the_context_menu_of_a_folder_offers_a_tab(qtbot, tree, window):
    folder = window._menus.for_entry(window._model.info(window._model.row_for_name("sub")))
    file = window._menus.for_entry(window._model.info(window._model.row_for_name("a.txt")))

    assert "Open in new tab" in [action.text() for action in folder.actions()]
    assert "Open in new tab" not in [action.text() for action in file.actions()]


def test_a_tab_can_be_split_on_its_own(qtbot, window):
    open_tab(qtbot, window)
    window.set_split(True)

    assert window.is_split
    assert not window._details.isVisible()

    window._tab_bar.setCurrentIndex(0)

    assert not window.is_split
    assert not window._actions.split_view.isChecked()
    assert window._details.isVisible()

    window._tab_bar.setCurrentIndex(1)

    assert window.is_split
    assert window._actions.split_view.isChecked()
    assert not window._details.isVisible()


def test_view_mode_and_hidden_files_reach_every_tab(qtbot, window):
    # One view for every folder, not one for each: see test_folder_views.py
    settings.set_per_folder(False)
    open_tab(qtbot, window)

    window.set_view_mode(settings.ICONS)
    window._actions.hidden.trigger()
    later = open_tab(qtbot, window)

    for tab in (*window._tabs[:2], later):
        assert tab.active.view.mode == settings.ICONS
        assert tab.active.proxy.show_hidden


def test_a_drop_on_a_tab_goes_into_its_folder(qtbot, tree, jobs, wait_final, window):
    open_tab(qtbot, window, tree / "two")
    shown = window._tab

    uris = [gfile(tree / "one" / "a.txt").get_uri()]
    window._tab_bar.dropped.emit(1, uris, Qt.KeyboardModifier.ControlModifier, QPoint(0, 0))
    job = jobs.jobs[0]
    wait_final(jobs, job)

    assert job.kind is JobKind.COPY
    assert os.listdir(tree / "two") == ["a.txt"]
    assert "a.txt" in os.listdir(tree / "one")
    # Dropping on a tab does not switch to it
    assert window._tab is shown


def test_a_drop_on_a_tab_moves_where_a_drop_on_the_list_would(
    qtbot, tree, jobs, wait_final, window
):
    open_tab(qtbot, window, tree / "two")

    uris = [gfile(tree / "one" / "a.txt").get_uri()]
    window._tab_bar.dropped.emit(1, uris, Qt.KeyboardModifier.NoModifier, QPoint(0, 0))
    settle(window)
    job = jobs.jobs[0]
    wait_final(jobs, job)

    assert job.kind is JobKind.MOVE
    assert os.listdir(tree / "two") == ["a.txt"]


def test_a_drive_that_goes_away_leaves_no_tab_on_it(qtbot, tree, window):
    open_tab(qtbot, window, tree / "two")

    window._on_volume_removed(gfile(tree))

    qtbot.waitUntil(
        lambda: all(path(tab.active) != str(tree / "one") for tab in window._tabs), timeout=TIMEOUT
    )
    assert all(not path(tab.active).startswith(str(tree)) for tab in window._tabs)


def test_narrowing_closes_the_splits_of_every_tab(qtbot, window):
    window.set_split(True)
    open_tab(qtbot, window)
    window.set_split(True)

    window.resize(NARROW, 600)
    qtbot.waitUntil(lambda: window.is_narrow, timeout=TIMEOUT)

    assert not any(tab.is_split for tab in window._tabs)
    # The tabs themselves stay, and so does their bar
    assert len(window._tabs) == 2
    assert window._tab_bar.isVisible()


def test_tabs_can_be_reordered(qtbot, tree, window):
    first = window._tab
    second = open_tab(qtbot, window, tree / "two")

    window._tab_bar.moveTab(0, 1)

    assert window._tabs == [second, first]
    assert window._tab is second
    window._tab_bar.setCurrentIndex(1)
    assert window._tab is first


def test_the_close_button_sits_in_the_middle_of_the_drawn_tab(qtbot, tree, window):
    open_tab(qtbot, window, tree / "two")
    window._tab_bar.repaint()
    bar = window._tab_bar
    gap = style.px(style.TAB_GAP)

    for index in range(bar.count()):
        button = bar.tabButton(index, bar.ButtonPosition.RightSide)
        tab = bar.tabRect(index)
        # The gap is not part of the drawn tab
        assert button.geometry().center().y() == pytest.approx(
            tab.top() + (tab.height() - gap) / 2, abs=1
        )
        assert button.geometry().right() < tab.right() - gap
