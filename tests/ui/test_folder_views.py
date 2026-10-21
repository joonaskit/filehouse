"""The view and the sort order: remembered, for the app and for each folder."""

import json

import pytest
from PySide6.QtCore import Qt
from shiboken6 import isValid
from ui_helpers import TIMEOUT, gfile

from filehouse.core import locations
from filehouse.integration import style
from filehouse.qt_models.directory_model import MODIFIED, NAME, SIZE
from filehouse.ui import main_window, settings
from filehouse.ui.main_window import MainWindow

DOWN, UP = Qt.SortOrder.DescendingOrder, Qt.SortOrder.AscendingOrder
WIDE = style.px(main_window.NARROW_BELOW) + 200


@pytest.fixture
def tree(tmp_path):
    for name in ("downloads", "pictures", "code"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "a.txt").write_text("a")
    return tmp_path


@pytest.fixture
def open_window(qtbot, jobs):
    windows = []

    def open_at(path, **options):
        window = MainWindow(gfile(path), jobs=jobs, **options)
        windows.append(window)
        window.resize(WIDE, 600)
        window.show()
        wait_loaded(qtbot, window)
        return window

    yield open_at
    for window in windows:
        if isValid(window):
            window.close()


def wait_loaded(qtbot, window):
    qtbot.waitUntil(lambda: not window._model.loading, timeout=TIMEOUT)


def go(qtbot, window, path):
    window.navigate(gfile(path))
    wait_loaded(qtbot, window)


def shown(window):
    return window._view.mode, window._view.sort_column(), window._view.sort_order()


def uri(path):
    return gfile(path).get_uri()


def test_a_new_window_sorts_as_the_app_does(tree, open_window):
    settings.set_sort("modified", True)
    settings.set_view_mode(settings.ICONS)

    window = open_window(tree / "code")

    assert shown(window) == (settings.ICONS, MODIFIED, DOWN)
    assert window._actions.icons_mode.isChecked()


def test_a_folder_keeps_what_was_chosen_in_it(qtbot, tree, open_window):
    window = open_window(tree / "downloads")

    window._view.sort(MODIFIED, DOWN)
    go(qtbot, window, tree / "pictures")
    assert shown(window) == (settings.DETAILS, NAME, UP)
    window._actions.icons_mode.trigger()
    go(qtbot, window, tree / "code")

    assert shown(window) == (settings.DETAILS, NAME, UP)
    assert window._actions.details_mode.isChecked()
    go(qtbot, window, tree / "downloads")
    assert shown(window) == (settings.DETAILS, MODIFIED, DOWN)
    go(qtbot, window, tree / "pictures")
    assert shown(window) == (settings.ICONS, NAME, UP)
    assert window._actions.icons_mode.isChecked()
    # The app's own choice is as it was
    assert settings.view_mode() == settings.DETAILS and settings.sort() == ("name", False)


def test_a_click_on_a_column_is_a_choice_too(qtbot, tree, open_window):
    window = open_window(tree / "downloads")

    window._view._tree.header().sortIndicatorChanged.emit(SIZE, DOWN)
    window._view._tree.sortByColumn(SIZE, DOWN)

    assert settings.folder_view(uri(tree / "downloads")) == {"sort": "size", "descending": True}


def test_it_is_remembered_between_runs_in_a_file_of_its_own(qtbot, tree, open_window):
    first = open_window(tree / "downloads")
    first._view.sort(SIZE, DOWN)
    first._actions.icons_mode.trigger()
    first.close()
    # As a new process reads it
    settings._folders = None

    second = open_window(tree / "downloads")

    assert shown(second) == (settings.ICONS, SIZE, DOWN)
    stored = json.loads(settings._folders_path().read_text())
    assert stored == {
        uri(tree / "downloads"): {"sort": "size", "descending": True, "mode": "icons"}
    }


def test_only_what_was_chosen_is_the_folders_own(qtbot, tree, open_window):
    window = open_window(tree / "pictures")
    window._actions.icons_mode.trigger()

    # The sort order was left alone there: it follows the app's
    settings.set_sort("size", True)

    assert shown(window) == (settings.ICONS, SIZE, DOWN)


def test_use_the_default_here_gives_the_folders_choice_up(qtbot, tree, open_window):
    window = open_window(tree / "pictures")
    assert not window._pane.has_own_view

    window._actions.icons_mode.trigger()
    window._view.sort(MODIFIED, DOWN)
    assert window._pane.has_own_view

    window._pane.use_default_view()

    assert shown(window) == (settings.DETAILS, NAME, UP)
    assert window._actions.details_mode.isChecked()
    assert not window._pane.has_own_view
    assert settings.folder_view(uri(tree / "pictures")) == {}


def test_the_view_menu_offers_the_way_back_only_where_folders_keep_theirs(tree, open_window):
    window = open_window(tree / "pictures")

    def view_entries():
        menu = window._menus.for_entry(None)
        view = next(action.menu() for action in menu.actions() if action.text() == "View")
        return {action.text(): action for action in view.actions()}

    assert not view_entries()["Use the default here"].isEnabled()
    window._actions.icons_mode.trigger()
    assert view_entries()["Use the default here"].isEnabled()

    settings.set_per_folder(False)
    assert "Use the default here" not in view_entries()


def test_with_one_view_for_all_a_change_is_everyones(qtbot, tree, open_window):
    settings.set_per_folder(False)
    window = open_window(tree / "downloads")
    other = open_window(tree / "code")

    window._view.sort(MODIFIED, DOWN)
    window._actions.icons_mode.trigger()

    # The other window follows at once, and so does every folder gone to
    assert shown(other) == (settings.ICONS, MODIFIED, DOWN)
    assert other._actions.icons_mode.isChecked()
    go(qtbot, window, tree / "pictures")
    assert shown(window) == (settings.ICONS, MODIFIED, DOWN)
    assert settings.sort() == ("modified", True) and settings.view_mode() == settings.ICONS
    assert settings.folder_view(uri(tree / "downloads")) == {}


def test_a_folders_own_view_waits_while_folders_do_not_keep_theirs(qtbot, tree, open_window):
    window = open_window(tree / "pictures")
    window._actions.icons_mode.trigger()

    settings.set_per_folder(False)
    assert shown(window)[0] == settings.DETAILS

    settings.set_per_folder(True)
    assert shown(window)[0] == settings.ICONS


def test_the_same_folder_is_shown_the_same_way_in_every_pane(qtbot, tree, open_window):
    window = open_window(tree / "pictures")
    window.set_split(True)
    first, second = window._panes
    second.navigate(gfile(tree / "code"))

    first.view.focused.emit()
    window._actions.icons_mode.trigger()

    assert first.view.mode == settings.ICONS and second.view.mode == settings.DETAILS
    # The buttons say how the active pane shows its folder
    second.view.focused.emit()
    assert window._actions.details_mode.isChecked()
    second.navigate(gfile(tree / "pictures"))
    assert second.view.mode == settings.ICONS


def test_the_recent_files_stay_the_latest_first(qtbot, tree, open_window, monkeypatch):
    # A plain folder stands in for the list of recent files
    recent = gfile(tree / "code")
    monkeypatch.setattr(locations, "in_recent", lambda location: location.equal(recent))
    window = open_window(tree / "downloads")
    window._view.sort(SIZE, UP)

    go(qtbot, window, tree / "code")
    assert shown(window)[1:] == (MODIFIED, DOWN)
    # A sort chosen there is for now, and nobody's choice
    window._view.sort(NAME, UP)
    assert settings.folder_view(uri(tree / "code")) == {}

    go(qtbot, window, tree / "downloads")
    assert shown(window)[1:] == (SIZE, UP)
    go(qtbot, window, tree / "code")
    assert shown(window)[1:] == (MODIFIED, DOWN)


def test_the_folders_changed_longest_ago_are_forgotten(monkeypatch):
    monkeypatch.setattr(settings, "MAX_FOLDERS", 3)

    for name in ("a", "b", "c"):
        settings.choose_view(f"file:///{name}", mode=settings.ICONS)
    # Changed again: no longer the oldest
    settings.choose_view("file:///a", sort=("size", True))
    settings.choose_view("file:///d", mode=settings.ICONS)

    assert settings.folder_view("file:///b") == {}
    assert settings.folder_view("file:///a") == {
        "mode": "icons",
        "sort": "size",
        "descending": True,
    }
    assert settings.folder_view("file:///d") == {"mode": "icons"}


def test_a_file_that_was_spoiled_is_not_trusted():
    path = settings._folders_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "file:///good": {"mode": "icons", "sort": "size", "descending": 1},
                "file:///odd": {"mode": "carousel", "sort": "colour"},
                "file:///worse": "icons",
            }
        )
    )
    settings._folders = None

    assert settings.view_for("file:///good") == (settings.ICONS, "size", True)
    assert settings.view_for("file:///odd") == (settings.DETAILS, "name", False)
    assert settings.view_for("file:///worse") == (settings.DETAILS, "name", False)

    path.write_text("not json")
    settings._folders = None
    assert settings.view_for("file:///good") == (settings.DETAILS, "name", False)
