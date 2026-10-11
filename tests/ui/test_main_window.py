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
