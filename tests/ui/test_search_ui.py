"""Search from the window: the field, the filter, and what is found below the folder."""

import pytest
from PySide6.QtCore import Qt
from shiboken6 import isValid
from ui_helpers import TIMEOUT, gfile

from filehouse.core import locations
from filehouse.core.operations import JobState
from filehouse.qt_models.directory_model import PERMISSIONS
from filehouse.ui.main_window import MainWindow

NARROW, WIDE = 560, 1000


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "work"
    (root / "docs" / "old").mkdir(parents=True)
    (root / ".cache").mkdir()
    (root / "report.txt").write_text("top")
    (root / "notes.txt").write_text("notes")
    (root / "docs" / "Report 2024.pdf").write_text("a")
    (root / "docs" / "summary.txt").write_text("b")
    (root / "docs" / "old" / "report-draft.txt").write_text("c")
    (root / ".cache" / "report.bin").write_text("d")
    return root


@pytest.fixture
def window(qtbot, jobs, tree):
    window = MainWindow(gfile(tree), jobs=jobs)
    window.resize(WIDE, 600)
    window.show()
    qtbot.waitUntil(lambda: not window._model.loading, timeout=TIMEOUT)
    yield window
    jobs.cancel_all()
    qtbot.waitUntil(lambda: jobs.active_count() == 0, timeout=TIMEOUT)
    if isValid(window):
        window.close()


def shown(window):
    proxy = window._proxy
    return sorted(
        window._model.info(proxy.mapToSource(proxy.index(row, 0)).row()).name
        for row in range(proxy.rowCount())
    )


def search(qtbot, window, text):
    window._search_field.setText(text)
    qtbot.waitUntil(lambda: not window._pane.searching, timeout=TIMEOUT)


def test_control_f_goes_to_the_field(qtbot, window):
    qtbot.keyClick(window._view.focusProxy(), Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)

    assert window._search_field.hasFocus()


def test_typing_filters_the_folder_at_once(qtbot, window):
    window._search_field.setText("not")

    # Before anything below the folder is looked at
    assert shown(window) == ["notes.txt"]
    assert window._pane.searching


def test_what_is_below_the_folder_is_found_too(qtbot, window):
    search(qtbot, window, "report")

    assert shown(window) == ["docs/Report 2024.pdf", "docs/old/report-draft.txt", "report.txt"]
    assert window._count_label.text() == "3 found"
    # The column says where each one is
    header = window._model.headerData(PERMISSIONS, Qt.Orientation.Horizontal)
    assert header == "Folder"
    row = window._model.row_for_name("docs/old/report-draft.txt")
    assert window._model.index(row, PERMISSIONS).data() == "docs/old"
    assert window._model.index(row, 0).data() == "report-draft.txt"


def test_the_words_can_come_in_any_order(qtbot, window):
    search(qtbot, window, "2024 rep")

    assert shown(window) == ["docs/Report 2024.pdf"]


def test_escape_ends_the_search(qtbot, window):
    search(qtbot, window, "report")
    window._search_field.setFocus()

    qtbot.keyClick(window._search_field, Qt.Key.Key_Escape)

    assert window._search_field.text() == ""
    assert shown(window) == ["docs", "notes.txt", "report.txt"]
    assert window._model.headerData(PERMISSIONS, Qt.Orientation.Horizontal) == "Permissions"
    assert window._count_label.text() == "3 items"
    assert not window._search_field.hasFocus()


def test_nothing_found_says_so(qtbot, window):
    search(qtbot, window, "zebra")

    assert window._view._message.isVisible()
    assert window._view._message.text() == "Nothing called “zebra” here"
    assert window._count_label.text() == "0 found"


def test_hidden_files_are_found_only_when_they_are_shown(qtbot, window):
    search(qtbot, window, "report")
    assert ".cache/report.bin" not in shown(window)

    window.set_show_hidden(True)
    qtbot.waitUntil(lambda: not window._pane.searching, timeout=TIMEOUT)

    assert ".cache/report.bin" in shown(window)


def test_going_to_another_folder_ends_the_search(qtbot, window, tree):
    search(qtbot, window, "report")

    window.navigate(gfile(tree / "docs"))
    qtbot.waitUntil(lambda: not window._model.loading, timeout=TIMEOUT)

    assert window._search_field.text() == ""
    assert shown(window) == ["Report 2024.pdf", "old", "summary.txt"]


def test_a_result_that_is_a_folder_opens_as_that_folder(qtbot, window, tree):
    search(qtbot, window, "old")
    window._view.select_name("docs/old")

    window._on_item_activated(window._view.selected_infos()[0])
    qtbot.waitUntil(lambda: not window._model.loading, timeout=TIMEOUT)

    assert locations.local_path(window._location) == str(tree / "docs" / "old")
    assert window._search_field.text() == ""


def test_show_in_folder_goes_to_where_a_result_is(qtbot, window, tree):
    search(qtbot, window, "draft")
    info = window._model.info(window._model.row_for_name("docs/old/report-draft.txt"))
    window._view.select_name(info.name)

    menu = window._menu_for(info)
    assert "Show in folder" in [action.text() for action in menu.actions()]
    window.show_in_folder(info)
    qtbot.waitUntil(lambda: not window._model.loading, timeout=TIMEOUT)

    assert locations.local_path(window._location) == str(tree / "docs" / "old")
    qtbot.waitUntil(
        lambda: [i.name for i in window._view.selected_infos()] == ["report-draft.txt"],
        timeout=TIMEOUT,
    )


def test_an_entry_of_the_folder_itself_has_no_show_in_folder(qtbot, window):
    search(qtbot, window, "report")
    info = window._model.info(window._model.row_for_name("report.txt"))

    assert "Show in folder" not in [action.text() for action in window._menu_for(info).actions()]


def test_the_actions_act_on_the_file_where_it_is(qtbot, window, tree, jobs, wait_final):
    search(qtbot, window, "draft")
    window._view.select_name("docs/old/report-draft.txt")

    assert [locations.local_path(location) for location in window._selected_locations()] == [
        str(tree / "docs" / "old" / "report-draft.txt")
    ]
    assert window._count_label.text().endswith("report-draft.txt in docs/old")


def test_renaming_a_result_offers_its_name_and_not_its_path(qtbot, window, tree):
    search(qtbot, window, "report")
    window._view.select_name("docs/old/report-draft.txt")

    dialog = window.rename()
    assert dialog.edit.text() == "report-draft.txt"
    assert dialog.edit.selectedText() == "report-draft"
    dialog.edit.setText("report-final.txt")
    dialog.accept()

    assert (tree / "docs" / "old" / "report-final.txt").exists()
    # Found again under its new name, in the search that is still on
    qtbot.waitUntil(lambda: "docs/old/report-final.txt" in shown(window), timeout=TIMEOUT)
    assert "docs/old/report-draft.txt" not in shown(window)


def test_a_result_that_a_job_took_away_goes_from_the_list(
    qtbot, window, tree, jobs, wait_final, home_dir
):
    search(qtbot, window, "draft")
    window._view.select_name("docs/old/report-draft.txt")

    job = window.delete_selected()
    box = None
    qtbot.waitUntil(lambda: jobs.prompter.current is not None, timeout=TIMEOUT)
    box = jobs.prompter.current
    from filehouse.core.operations import Answer

    box.button_for(Answer.DELETE).click()
    wait_final(jobs, job)

    assert job.state is JobState.DONE
    assert not (tree / "docs" / "old" / "report-draft.txt").exists()
    qtbot.waitUntil(lambda: shown(window) == [], timeout=TIMEOUT)


def test_each_pane_has_its_own_search(qtbot, window, tree):
    search(qtbot, window, "report")
    first = window._pane

    window.set_split(True)
    other = window._other_pane()
    qtbot.waitUntil(lambda: not other.model.loading, timeout=TIMEOUT)
    assert window._search_field.text() == "report"

    window._activate(other)
    assert window._search_field.text() == ""
    assert other.proxy.rowCount() == 3

    window._activate(first)
    assert window._search_field.text() == "report"


def test_in_a_narrow_window_the_field_takes_the_place_of_the_path(qtbot, window):
    window.resize(NARROW, 600)
    # The toolbar puts its widgets away when it next lays itself out
    qtbot.waitUntil(lambda: not window._search_group.isVisible(), timeout=TIMEOUT)
    assert window._path_bar.isVisible()

    window._search.trigger()

    qtbot.waitUntil(lambda: not window._path_bar.isVisible(), timeout=TIMEOUT)
    assert window._search_group.isVisible()
    assert window._search_field.hasFocus()

    qtbot.keyClick(window._search_field, Qt.Key.Key_Escape)

    qtbot.waitUntil(lambda: not window._search_group.isVisible(), timeout=TIMEOUT)
    assert window._path_bar.isVisible()


def test_in_a_narrow_window_the_field_stays_while_a_search_is_on(qtbot, window):
    window.resize(NARROW, 600)
    window._search.trigger()
    search(qtbot, window, "report")

    qtbot.keyClick(window._search_field, Qt.Key.Key_Down)

    assert not window._search_field.hasFocus()
    assert window._search_group.isVisible()
    assert len(shown(window)) == 3


def test_a_wide_window_always_has_the_field(window):
    assert window._search_group.isVisible()
    assert window._path_bar.isVisible()


def test_reloading_keeps_the_search(qtbot, window, tree):
    search(qtbot, window, "report")
    (tree / "docs" / "report-new.txt").write_text("new")

    window._pane.reload()
    qtbot.waitUntil(
        lambda: not window._model.loading and not window._pane.searching, timeout=TIMEOUT
    )

    assert window._search_field.text() == "report"
    assert "docs/report-new.txt" in shown(window)
