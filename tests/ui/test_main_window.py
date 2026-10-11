import os

import pytest
from gi.repository import Gio
from PySide6.QtCore import Qt

from filehouse.ui.main_window import MainWindow


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "Downloads").mkdir()
    (tmp_path / "Downloads" / "sub").mkdir()
    (tmp_path / "Downloads" / "report.pdf").write_text("")
    return tmp_path


def open_window(qtbot, path, **kwargs):
    window = MainWindow(Gio.File.new_for_path(str(path)), **kwargs)
    qtbot.addWidget(window)
    wait_loaded(qtbot, window)
    return window


def wait_loaded(qtbot, window):
    qtbot.waitUntil(lambda: not window._model.loading, timeout=5000)


def current_path(window):
    return window._location.get_path()


def test_title_names_the_folder(qtbot, tree):
    window = open_window(qtbot, tree / "Downloads")

    assert window.windowTitle() == "Downloads — Files"
    assert window._count_label.text() == "2 items"


def test_title_for_home_and_root(qtbot):
    window = open_window(qtbot, "/")
    assert window.windowTitle() == "/ — Files"
    assert not window._up.isEnabled()


def test_up_back_and_forward(qtbot, tree):
    window = open_window(qtbot, tree / "Downloads" / "sub")
    assert not window._back.isEnabled()

    window.go_up()
    wait_loaded(qtbot, window)
    assert current_path(window) == str(tree / "Downloads")
    assert window.windowTitle() == "Downloads — Files"
    # The folder we came from is selected
    assert window._view.selected_count() == 1

    window.go_back()
    assert current_path(window) == str(tree / "Downloads" / "sub")
    assert window._forward.isEnabled()

    window.go_forward()
    assert current_path(window) == str(tree / "Downloads")


def test_file_argument_is_selected(qtbot, tree):
    window = open_window(qtbot, tree / "Downloads", select="report.pdf")

    selected = window._view.selection_model().selectedRows()
    assert [index.data() for index in selected] == ["report.pdf"]


def test_activating_a_folder_enters_it(qtbot, tree):
    window = open_window(qtbot, tree / "Downloads")
    info = window._model.info(window._model.row_for_name("sub"))

    window._on_item_activated(info)
    wait_loaded(qtbot, window)

    assert current_path(window) == str(tree / "Downloads" / "sub")
    assert window._view._message.text() == "This folder is empty"
    assert window._view._stack.currentWidget() is window._view._message


def test_typed_path_navigates_and_a_bad_one_stays_put(qtbot, tree):
    window = open_window(qtbot, tree)

    window._on_path_entered(str(tree / "Downloads"))
    assert current_path(window) == str(tree / "Downloads")

    window._on_path_entered(str(tree / "missing"))
    assert current_path(window) == str(tree / "Downloads")
    assert "missing" in window.statusBar().currentMessage()


def test_unreadable_folder_shows_the_reason(qtbot, tree):
    window = open_window(qtbot, tree)

    window.navigate(Gio.File.new_for_path(str(tree / "gone")))
    wait_loaded(qtbot, window)

    assert window._view._stack.currentWidget() is window._view._message
    assert window._view._message.text()


def test_hidden_files_toggle(qtbot, tree):
    (tree / "Downloads" / ".secret").write_text("")
    window = open_window(qtbot, tree / "Downloads")
    assert window._count_label.text() == "2 items"

    window._hidden.trigger()

    assert window._count_label.text() == "3 items"


def test_ctrl_l_edits_the_location(qtbot, tree):
    window = open_window(qtbot, tree / "Downloads")
    window.show()
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow)

    qtbot.keyClick(window, Qt.Key.Key_L, Qt.KeyboardModifier.ControlModifier)

    assert window._path_bar.currentWidget() is window._path_bar._edit
    assert window._path_bar._edit.text() == str(tree / "Downloads")


def test_a_folder_whose_last_file_goes_says_it_is_empty(qtbot, tree):
    window = open_window(qtbot, tree / "Downloads" / "sub")
    (tree / "Downloads" / "sub" / "only.txt").write_text("")
    qtbot.waitUntil(lambda: window._count_label.text() == "1 item", timeout=5000)

    (tree / "Downloads" / "sub" / "only.txt").unlink()

    qtbot.waitUntil(lambda: window._count_label.text() == "0 items", timeout=5000)
    assert window._view._message.text() == "This folder is empty"


def test_status_names_a_single_selection_and_counts_several(qtbot, tree):
    window = open_window(qtbot, tree / "Downloads")
    model = window._view.selection_model()
    flags = model.SelectionFlag.Select | model.SelectionFlag.Rows

    model.select(window._proxy.index(0, 0), flags)
    assert window._count_label.text() == "2 items · sub selected"

    model.select(window._proxy.index(1, 0), flags)
    assert window._count_label.text() == "2 items · 2 selected"


def test_toolbar_is_made_of_groups_with_icon_buttons(qtbot, tree):
    from PySide6.QtWidgets import QFrame, QToolBar, QToolButton

    window = open_window(qtbot, tree / "Downloads")

    toolbar = window.findChild(QToolBar)
    groups = [frame for frame in toolbar.findChildren(QFrame) if frame.objectName() == "group"]
    buttons = [
        [
            button.defaultAction().text()
            for button in group.findChildren(QToolButton)
            # The breadcrumbs are buttons too, without an action
            if button.defaultAction() is not None
        ]
        for group in groups
    ]

    assert buttons == [
        ["Back", "Forward", "Up"],
        [],
        ["Copy", "Move to…", "Rename…", "Move to trash"],
        ["Details", "Icons"],
        # Only shown in a narrow window
        ["Folder menu"],
    ]
    # The location sits in a group of its own
    assert window._path_bar.parent() is groups[1]


def test_breadcrumbs_fit_in_the_path_bar_and_match_the_icon_buttons(qtbot, tree):
    from PySide6.QtWidgets import QToolButton

    window = open_window(qtbot, tree / "Downloads")
    window.show()
    qtbot.waitExposed(window)
    bar = window._path_bar
    crumbs = bar.findChildren(QToolButton)
    back = next(
        button
        for button in window.findChildren(QToolButton)
        if button.defaultAction() is window._back
    )

    assert crumbs
    for crumb in crumbs:
        top = crumb.mapTo(bar, crumb.rect().topLeft()).y()
        # Wholly inside the bar: a taller button has its hover highlight cut off
        assert top >= 0 and top + crumb.height() <= bar.height()
        assert crumb.height() == back.height()
    assert bar.parent().height() == back.parent().height()


def test_select_all_selects_what_is_shown(qtbot, tree):
    (tree / "Downloads" / ".secret").write_text("")
    window = open_window(qtbot, tree / "Downloads")

    window._select_all.trigger()

    assert [info.name for info in window._view.selected_infos()] == ["sub", "report.pdf"]
    assert window._count_label.text() == "2 items · 2 selected"


def test_select_all_works_in_the_icons_too(qtbot, tree):
    window = open_window(qtbot, tree / "Downloads")
    window.set_view_mode("icons")

    window._select_all.trigger()

    assert window._view.selected_count() == 2


def submenu(menu, title):
    return next(a.menu() for a in menu.actions() if a.menu() and a.menu().title() == title)


def entry(menu, text):
    return next(a for a in menu.actions() if a.text() == text)


def test_folder_menu_sorts_the_list(qtbot, tree):
    from PySide6.QtWidgets import QMenu

    from filehouse.qt_models.directory_model import MODIFIED, NAME

    (tree / "Downloads" / "aaa.txt").write_text("")
    os.utime(tree / "Downloads" / "report.pdf", (1_000_000, 1_000_000))
    window = open_window(qtbot, tree / "Downloads")
    menu = QMenu(window)
    window._add_sort_and_view(menu)
    sort = submenu(menu, "Sort by")
    assert entry(sort, "Name").isChecked() and entry(sort, "Ascending").isChecked()
    assert not entry(sort, "Modified").isChecked()

    # Newest first, folders still on top
    entry(sort, "Modified").trigger()

    assert (window._view.sort_column(), window._view.sort_order()) == (
        MODIFIED,
        Qt.SortOrder.DescendingOrder,
    )
    shown = [window._proxy.index(row, NAME).data() for row in range(window._proxy.rowCount())]
    assert shown == ["sub", "aaa.txt", "report.pdf"]

    # A menu made now shows the new order, and can turn it around
    menu = QMenu(window)
    window._add_sort_and_view(menu)
    sort = submenu(menu, "Sort by")
    assert entry(sort, "Modified").isChecked() and entry(sort, "Descending").isChecked()
    entry(sort, "Ascending").trigger()
    shown = [window._proxy.index(row, NAME).data() for row in range(window._proxy.rowCount())]
    assert shown == ["sub", "report.pdf", "aaa.txt"]


def test_folder_menu_changes_the_view(qtbot, tree):
    from PySide6.QtWidgets import QMenu

    window = open_window(qtbot, tree / "Downloads")
    menu = QMenu(window)
    window._add_sort_and_view(menu)

    entry(submenu(menu, "View"), "Icons").trigger()

    assert window._view.mode == "icons"


def test_folders_are_counted_again_when_the_jobs_are_done(qtbot, tree):
    window = open_window(qtbot, tree / "Downloads")
    window._model._count_timer.setInterval(0)
    size = lambda: window._proxy.index(0, 3).data()  # noqa: E731
    qtbot.waitUntil(lambda: size() == "0 items", timeout=5000)
    (tree / "Downloads" / "sub" / "new.txt").write_text("")

    window._jobs.idle.emit()

    qtbot.waitUntil(lambda: size() == "1 item", timeout=5000)


def test_details_pane_follows_the_selection(qtbot, tree):
    window = open_window(qtbot, tree / "Downloads")
    window.show()
    pane = window._details
    qtbot.waitUntil(lambda: pane.name.toolTip() == "Downloads", timeout=5000)
    qtbot.waitUntil(lambda: pane.rows["Size"].text() == "2", timeout=5000)

    window._view.select_name("report.pdf")
    qtbot.waitUntil(lambda: pane.name.toolTip() == "report.pdf", timeout=5000)
    assert pane.open_button.isVisible()

    window._select_all.trigger()
    qtbot.waitUntil(lambda: pane.name.toolTip() == "2 items", timeout=5000)

    window._view.selection_model().clear()
    qtbot.waitUntil(lambda: pane.name.toolTip() == "Downloads", timeout=5000)


def test_details_pane_shows_the_folder_gone_to(qtbot, tree):
    window = open_window(qtbot, tree / "Downloads")
    qtbot.waitUntil(lambda: window._details.name.toolTip() == "Downloads", timeout=5000)

    window.navigate(Gio.File.new_for_path(str(tree / "Downloads" / "sub")))

    qtbot.waitUntil(lambda: window._details.name.toolTip() == "sub", timeout=5000)
    qtbot.waitUntil(lambda: window._details.rows["Size"].text() == "0", timeout=5000)


def test_details_pane_sees_a_selected_file_change(qtbot, tree):
    window = open_window(qtbot, tree / "Downloads")
    window.show()
    pane = window._details
    window._view.select_name("report.pdf")
    qtbot.waitUntil(lambda: pane.name.toolTip() == "report.pdf", timeout=5000)
    empty = pane.rows["Size"].text()

    (tree / "Downloads" / "report.pdf").write_text("x" * 5000)

    qtbot.waitUntil(lambda: pane.rows["Size"].text() != empty, timeout=5000)


def test_open_in_the_details_pane_enters_the_selected_folder(qtbot, tree):
    window = open_window(qtbot, tree / "Downloads")
    window.show()
    window._view.select_name("sub")
    qtbot.waitUntil(lambda: window._details.open_button.isVisible(), timeout=5000)

    window._details.open_button.click()

    assert current_path(window) == str(tree / "Downloads" / "sub")
