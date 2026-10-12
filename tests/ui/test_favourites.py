"""The favourites: Dolphin's places as a bar under the toolbar."""

import os

import pytest
from PySide6.QtCore import QPoint, Qt
from ui_helpers import TIMEOUT, gfile

from filehouse.core.operations import JobKind
from filehouse.integration import places
from filehouse.qt_models.favourites import Favourites
from filehouse.ui import main_window as main_window_module
from filehouse.ui.main_window import MainWindow

SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xbel>
<xbel xmlns:bookmark="http://www.freedesktop.org/standards/desktop-bookmarks">
 <bookmark href="file:///home/me">
  <title>Home</title>
  <info>
   <metadata owner="http://www.kde.org">
    <ID>1789116079/0</ID>
    <isSystemItem>true</isSystemItem>
   </metadata>
  </info>
 </bookmark>
 <separator>
  <info><metadata owner="http://www.kde.org"><UDI>/dev/x</UDI></metadata></info>
 </separator>
</xbel>
"""


@pytest.fixture
def tree(tmp_path):
    for name in ("alpha", "beta", "gamma"):
        (tmp_path / "folders" / name).mkdir(parents=True)
    (tmp_path / "folders" / "alpha" / "a.txt").write_text("a")
    (tmp_path / "folders" / "note.txt").write_text("x")
    return tmp_path / "folders"


@pytest.fixture
def places_file(tmp_path):
    file = tmp_path / "data" / "user-places.xbel"
    file.parent.mkdir()
    file.write_text(SAMPLE)
    return file


@pytest.fixture
def favourites(qtbot, places_file):
    return Favourites(str(places_file), settle_ms=20)


@pytest.fixture
def window(qtbot, tree, jobs, favourites):
    window = MainWindow(gfile(tree), jobs=jobs, favourites=favourites)
    qtbot.addWidget(window)
    window.resize(1000, 600)
    window.show()
    qtbot.waitUntil(lambda: not window._model.loading, timeout=TIMEOUT)
    return window


def titles(favourites):
    return [item.title for item in favourites.items]


# The list


def test_there_are_none_to_begin_with(favourites):
    assert favourites.items == []
    assert favourites.available


def test_a_folder_is_added_and_found(qtbot, favourites, tree):
    with qtbot.waitSignal(favourites.changed, timeout=TIMEOUT):
        assert favourites.add(gfile(tree / "alpha"))

    assert titles(favourites) == ["alpha"]
    found = favourites.find(gfile(tree / "alpha"))
    assert found.location.get_path() == str(tree / "alpha")
    assert favourites.find(gfile(tree / "beta")) is None


def test_it_is_renamed_moved_and_removed(qtbot, favourites, tree):
    favourites.add(gfile(tree / "alpha"))
    favourites.add(gfile(tree / "beta"))
    alpha, beta = favourites.items

    assert favourites.rename(alpha, "First")
    assert favourites.move(favourites.items[1], -1)
    assert titles(favourites) == ["beta", "First"]
    assert favourites.remove(favourites.items[0])
    assert titles(favourites) == ["First"]


def test_a_change_made_by_another_app_is_followed(qtbot, favourites, places_file, tree):
    other = places.PlacesFile(str(places_file))

    with qtbot.waitSignal(favourites.changed, timeout=TIMEOUT):
        other.add(gfile(tree / "gamma").get_uri(), "Gamma")

    assert titles(favourites) == ["Gamma"]


def test_without_a_places_file_there_are_none_and_none_can_be_made(qtbot, tmp_path, tree):
    favourites = Favourites(str(tmp_path / "no-such" / "user-places.xbel"))

    assert not favourites.available
    assert favourites.items == []
    assert not favourites.add(gfile(tree))


# The bar


def test_the_bar_shows_only_when_there_is_a_favourite(qtbot, window, favourites, tree):
    assert not window._favourites_bar.isVisible()

    favourites.add(gfile(tree / "alpha"))

    assert window._favourites_bar.isVisible()
    assert [a.text() for a in window._favourites_bar._favourites] == ["alpha"]

    favourites.remove(favourites.items[0])

    assert not window._favourites_bar.isVisible()


def test_a_window_without_favourites_has_no_bar(qtbot, tree, jobs):
    window = MainWindow(gfile(tree), jobs=jobs)
    qtbot.addWidget(window)
    window.show()

    assert not window._favourites_bar.isVisible()
    assert not window._favourite.isEnabled()
    assert window.toggle_favourite() is None


def test_a_click_goes_there(qtbot, window, favourites, tree):
    favourites.add(gfile(tree / "beta"))
    action = next(iter(window._favourites_bar._favourites))

    action.trigger()

    assert window._location.get_path() == str(tree / "beta")


def test_a_middle_click_opens_a_tab_behind(qtbot, window, favourites, tree):
    favourites.add(gfile(tree / "beta"))
    action = next(iter(window._favourites_bar._favourites))
    button = window._favourites_bar.widgetForAction(action)

    qtbot.mouseClick(button, Qt.MouseButton.MiddleButton)

    assert len(window._tabs) == 2
    assert window._tab is window._tabs[0]
    assert window._tabs[1].active.location.get_path() == str(tree / "beta")


def test_the_bar_hides_in_a_narrow_window(qtbot, window, favourites, tree):
    favourites.add(gfile(tree / "alpha"))
    assert window._favourites_bar.isVisible()

    window.resize(500, 600)
    qtbot.waitUntil(lambda: window.is_narrow, timeout=TIMEOUT)

    assert not window._favourites_bar.isVisible()

    window.resize(1000, 600)
    qtbot.waitUntil(lambda: not window.is_narrow, timeout=TIMEOUT)

    assert window._favourites_bar.isVisible()


# Adding and taking out


def test_the_key_adds_the_open_folder_and_again_takes_it_out(qtbot, window, favourites, tree):
    assert window._favourite.text() == "Add to favourites"

    qtbot.keyClick(window._view, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)

    assert titles(favourites) == ["folders"]
    assert "added to the favourites" in window.statusBar().currentMessage()
    assert window._favourite.text() == "Remove from favourites"

    qtbot.keyClick(window._view, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier)

    assert titles(favourites) == []
    assert "taken out" in window.statusBar().currentMessage()
    assert window._favourite.text() == "Add to favourites"


def test_a_folder_of_the_list_has_its_own_entry(qtbot, window, favourites, tree):
    info = window._model.info(window._model.row_for_name("alpha"))

    menu = window._menu_for(info)
    texts = [a.text() for a in menu.actions()]
    assert "Add to favourites" in texts

    next(a for a in menu.actions() if a.text() == "Add to favourites").trigger()

    assert titles(favourites) == ["alpha"]
    texts = [a.text() for a in window._menu_for(info).actions()]
    assert "Remove from favourites" in texts
    # A file has none
    file = window._model.info(window._model.row_for_name("note.txt"))
    assert not any("favourites" in a.text() for a in window._menu_for(file).actions())


def test_the_menu_of_the_folder_has_it(qtbot, window):
    assert window._favourite in window._menu_for(None).actions()


def test_not_the_trash_or_the_recent_files(qtbot, window):
    from filehouse.core import locations

    window.navigate(locations.recent())
    qtbot.waitUntil(lambda: not window._model.loading, timeout=TIMEOUT)

    assert not window._favourite.isEnabled()
    assert window.toggle_favourite() is None


def test_without_a_places_file_it_says_where_it_lives(qtbot, tmp_path, tree, jobs):
    favourites = Favourites(str(tmp_path / "nowhere" / "user-places.xbel"))
    window = MainWindow(gfile(tree), jobs=jobs, favourites=favourites)
    qtbot.addWidget(window)
    window.show()

    assert window.toggle_favourite() is None

    assert "Dolphin" in window.statusBar().currentMessage()


# Dropping


def test_files_dropped_on_a_favourite_go_into_it(qtbot, window, favourites, tree, jobs, wait_final):
    favourites.add(gfile(tree / "beta"))
    favourite = favourites.items[0]
    uris = [gfile(tree / "note.txt").get_uri()]

    window._favourites_bar.dropped.emit(
        favourite, uris, Qt.KeyboardModifier.NoModifier, QPoint(0, 0)
    )
    job = jobs.jobs[0]
    wait_final(jobs, job)

    # On one file system: a move, as for any drop
    assert job.kind is JobKind.MOVE
    assert os.listdir(tree / "beta") == ["note.txt"]
    # Where the window is does not change
    assert window._location.get_path() == str(tree)


def test_a_folder_dropped_on_itself_does_nothing(qtbot, window, favourites, tree, jobs):
    favourites.add(gfile(tree / "beta"))
    uris = [gfile(tree / "beta").get_uri()]

    window._favourites_bar.dropped.emit(
        favourites.items[0], uris, Qt.KeyboardModifier.NoModifier, QPoint(0, 0)
    )

    assert jobs.jobs == []


def test_folders_dropped_on_the_empty_part_are_added_and_files_are_not(
    qtbot, window, favourites, tree
):
    favourites.add(gfile(tree / "alpha"))
    uris = [
        gfile(tree / "beta").get_uri(),
        gfile(tree / "note.txt").get_uri(),
        gfile(tree / "alpha").get_uri(),
    ]

    window._favourites_bar.folders_dropped.emit(uris)

    assert titles(favourites) == ["alpha", "beta"]


# The menu of a favourite


def pick(window, favourite, text, monkeypatch):
    """Open the menu of a favourite without showing it, and trigger one entry."""
    shown = []

    def build(parent, title=None):
        menu = real(parent, title) if title else real(parent)
        menu.exec = lambda position: shown.append(menu) or None
        return menu

    real = main_window_module.menus.menu
    monkeypatch.setattr(main_window_module.menus, "menu", build)
    window._show_favourite_menu(favourite, QPoint(0, 0))
    menu = shown[0]
    return menu, next(a for a in menu.actions() if a.text() == text)


def test_its_menu_removes_it(qtbot, window, favourites, tree, monkeypatch):
    favourites.add(gfile(tree / "alpha"))
    menu, remove = pick(window, favourites.items[0], "Remove from favourites", monkeypatch)

    remove.trigger()

    assert titles(favourites) == []


def test_its_menu_moves_it_and_knows_the_ends(qtbot, window, favourites, tree, monkeypatch):
    favourites.add(gfile(tree / "alpha"))
    favourites.add(gfile(tree / "beta"))
    first = favourites.items[0]

    menu, left = pick(window, first, "Move left", monkeypatch)
    assert not left.isEnabled()
    menu, right = pick(window, first, "Move right", monkeypatch)
    assert right.isEnabled()
    right.trigger()

    assert titles(favourites) == ["beta", "alpha"]
    # Alpha is last now
    menu, right = pick(window, first, "Move right", monkeypatch)
    assert not right.isEnabled()


def test_its_menu_renames_it(qtbot, window, favourites, tree):
    favourites.add(gfile(tree / "alpha"))

    dialog = window.rename_favourite(favourites.items[0])
    result = dialog._apply("Photos")

    assert result is None
    assert titles(favourites) == ["Photos"]
    assert dialog._apply("   ") == "A favourite needs a name."
    dialog.close()


def test_its_menu_opens_it_in_a_tab(qtbot, window, favourites, tree, monkeypatch):
    favourites.add(gfile(tree / "gamma"))
    menu, open_in_tab = pick(window, favourites.items[0], "Open in new tab", monkeypatch)

    open_in_tab.trigger()

    assert window._tabs[1].active.location.get_path() == str(tree / "gamma")
