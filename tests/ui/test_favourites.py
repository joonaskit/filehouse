"""The favourites: Dolphin's places as a bar under the toolbar."""

import os

import pytest
from PySide6.QtCore import QPoint, Qt
from ui_helpers import TIMEOUT, gfile

from filehouse.core.operations import JobKind
from filehouse.integration import places
from filehouse.qt_models.favourites import Favourites
from filehouse.ui import main_window as main_window_module
from filehouse.ui import sidebar as sidebar_module
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


# The group in the sidebar


def sidebar_names(window):
    sidebar = window._sidebar
    return [sidebar.item(row).text() for row in range(sidebar.count())]


def favourite_row(window, name):
    sidebar = window._sidebar
    return next(
        row
        for row in range(sidebar.count())
        if sidebar.item(row).text() == name
        and sidebar.item(row).data(sidebar_module._FavouriteRole)
    )


def click_row(qtbot, window, row, button=Qt.MouseButton.LeftButton):
    sidebar = window._sidebar
    rect = sidebar.visualItemRect(sidebar.item(row))
    qtbot.mouseClick(sidebar.viewport(), button, pos=rect.center())


def test_the_group_says_what_it_is_for_while_it_is_empty(qtbot, window):
    names = sidebar_names(window)

    assert "▾ Favourites" in names
    # Between the places and the devices
    assert names.index("▾ Favourites") > names.index("▾ Places")
    assert "Drag a folder here" in names


def test_without_a_places_file_the_hint_says_where_it_comes_from(qtbot, tmp_path, tree, jobs):
    favourites = Favourites(str(tmp_path / "nowhere" / "user-places.xbel"))
    window = MainWindow(gfile(tree), jobs=jobs, favourites=favourites)
    qtbot.addWidget(window)

    assert "Not available yet" in sidebar_names(window)
    row = sidebar_names(window).index("Not available yet")
    assert "Open Dolphin once" in window._sidebar.item(row).toolTip()


def test_a_favourite_is_listed_under_its_heading(qtbot, window, favourites, tree):
    favourites.add(gfile(tree / "alpha"))
    favourites.add(gfile(tree / "beta"))

    names = sidebar_names(window)

    head = names.index("▾ Favourites")
    assert names[head + 1 : head + 3] == ["alpha", "beta"]
    assert "Drag a folder here" not in names
    item = window._sidebar.item(favourite_row(window, "alpha"))
    assert item.toolTip() == str(tree / "alpha")


def test_a_window_without_favourites_has_no_group(qtbot, tree, jobs):
    window = MainWindow(gfile(tree), jobs=jobs)
    qtbot.addWidget(window)

    assert "▾ Favourites" not in sidebar_names(window)
    assert not window._favourite.isEnabled()
    assert window.toggle_favourite() is None


def test_a_click_goes_there(qtbot, window, favourites, tree):
    favourites.add(gfile(tree / "beta"))

    click_row(qtbot, window, favourite_row(window, "beta"))

    assert window._location.get_path() == str(tree / "beta")


def test_a_middle_click_opens_a_tab_behind(qtbot, window, favourites, tree):
    favourites.add(gfile(tree / "beta"))

    click_row(qtbot, window, favourite_row(window, "beta"), Qt.MouseButton.MiddleButton)

    assert len(window._tabs) == 2
    assert window._tab is window._tabs[0]
    assert window._tabs[1].active.location.get_path() == str(tree / "beta")


def test_the_group_folds_shut_and_stays_so(qtbot, window, favourites, tree):
    favourites.add(gfile(tree / "alpha"))

    click_row(qtbot, window, sidebar_names(window).index("▾ Favourites"))

    names = sidebar_names(window)
    assert "▸ Favourites" in names and "alpha" not in names
    other = MainWindow(gfile(tree), jobs=window._jobs, favourites=favourites)
    qtbot.addWidget(other)
    assert "▸ Favourites" in [other._sidebar.item(r).text() for r in range(other._sidebar.count())]

    click_row(qtbot, window, sidebar_names(window).index("▸ Favourites"))

    assert "alpha" in sidebar_names(window)


def test_in_a_narrow_window_the_favourites_are_icons_on_the_rail(qtbot, window, favourites, tree):
    favourites.add(gfile(tree / "alpha"))

    window.resize(500, 600)
    qtbot.waitUntil(lambda: window.is_narrow, timeout=TIMEOUT)

    sidebar = window._sidebar
    row = sidebar.row_of("alpha")
    assert row >= 0
    assert sidebar.item(row).text() == ""
    assert sidebar.item(row).toolTip() == "alpha"


def test_the_favourite_of_the_open_folder_is_highlighted(qtbot, window, favourites, tree):
    favourites.add(gfile(tree / "alpha"))

    window.navigate(gfile(tree / "alpha"))

    assert window._sidebar.currentRow() == favourite_row(window, "alpha")


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


def drop(window, row, uris):
    """Drop URIs on a row of the sidebar, as a drag from another window would."""
    from PySide6.QtCore import QMimeData, QPointF, QUrl
    from PySide6.QtGui import QDropEvent

    sidebar = window._sidebar
    mime = QMimeData()
    mime.setUrls([QUrl(uri) for uri in uris])
    point = QPointF(sidebar.visualItemRect(sidebar.item(row)).center())
    event = QDropEvent(
        point,
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    sidebar.dropEvent(event)
    return event


def test_files_dropped_on_a_favourite_go_into_it(qtbot, window, favourites, tree, jobs, wait_final):
    favourites.add(gfile(tree / "beta"))
    uris = [gfile(tree / "note.txt").get_uri()]

    event = drop(window, favourite_row(window, "beta"), uris)
    job = jobs.jobs[0]
    wait_final(jobs, job)

    assert event.isAccepted()
    # On one file system: a move, as for any drop
    assert job.kind is JobKind.MOVE
    assert os.listdir(tree / "beta") == ["note.txt"]
    # Where the window is does not change
    assert window._location.get_path() == str(tree)


def test_files_can_be_dropped_on_a_place_too(qtbot, window, tree, jobs, wait_final, tmp_path):
    from filehouse.core import locations

    folder = tmp_path / "dropped-on"
    folder.mkdir()
    window._sidebar._places.append(locations.Place("Target", "folder", gfile(folder)))
    window._sidebar._fill()
    uris = [gfile(tree / "note.txt").get_uri()]

    drop(window, window._sidebar.row_of("Target"), uris)
    wait_final(jobs, jobs.jobs[0])

    assert os.listdir(folder) == ["note.txt"]


def test_a_folder_dropped_on_itself_does_nothing(qtbot, window, favourites, tree, jobs):
    favourites.add(gfile(tree / "beta"))
    uris = [gfile(tree / "beta").get_uri()]

    drop(window, favourite_row(window, "beta"), uris)

    assert jobs.jobs == []


def test_nothing_is_dropped_into_the_recent_files(qtbot, window, tree, jobs):
    from filehouse.core import locations

    if not locations.has_recent():
        pytest.skip("no recent files here")
    drop(window, window._sidebar.row_of("Recent"), [gfile(tree / "note.txt").get_uri()])

    assert jobs.jobs == []
    assert (tree / "note.txt").exists()


def test_folders_dropped_on_the_heading_are_added_and_files_are_not(
    qtbot, window, favourites, tree
):
    favourites.add(gfile(tree / "alpha"))
    uris = [
        gfile(tree / "beta").get_uri(),
        gfile(tree / "note.txt").get_uri(),
        gfile(tree / "alpha").get_uri(),
    ]

    drop(window, sidebar_names(window).index("▾ Favourites"), uris)

    assert titles(favourites) == ["alpha", "beta"]


def test_a_folder_dropped_on_the_hint_is_added(qtbot, window, favourites, tree):
    row = sidebar_names(window).index("Drag a folder here")

    drop(window, row, [gfile(tree / "gamma").get_uri()])

    assert titles(favourites) == ["gamma"]


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

    menu, left = pick(window, first, "Move up", monkeypatch)
    assert not left.isEnabled()
    menu, right = pick(window, first, "Move down", monkeypatch)
    assert right.isEnabled()
    right.trigger()

    assert titles(favourites) == ["beta", "alpha"]
    # Alpha is last now
    menu, right = pick(window, first, "Move down", monkeypatch)
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


# Dragging a favourite to another place in the group


def drag_favourite(window, favourite, row, upper):
    """Drop a favourite on a row of the sidebar, on its upper or lower half."""
    from PySide6.QtCore import QMimeData, QPointF
    from PySide6.QtGui import QDropEvent

    sidebar = window._sidebar
    mime = QMimeData()
    mime.setData(sidebar_module.FAVOURITE_MIME, favourite.id.encode())
    rect = sidebar.visualItemRect(sidebar.item(row))
    point = QPointF(rect.center().x(), rect.top() + 3 if upper else rect.bottom() - 3)
    event = QDropEvent(
        point,
        Qt.DropAction.MoveAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    sidebar.dropEvent(event)
    return event


@pytest.fixture
def three(favourites, tree):
    for name in ("alpha", "beta", "gamma"):
        favourites.add(gfile(tree / name))
    return favourites


def test_only_favourites_can_be_picked_up(qtbot, window, three):
    sidebar = window._sidebar
    for row in range(sidebar.count()):
        item = sidebar.item(row)
        draggable = bool(item.flags() & Qt.ItemFlag.ItemIsDragEnabled)
        assert draggable == (item.data(sidebar_module._FavouriteRole) is not None), item.text()


def test_a_dragged_favourite_carries_its_id_and_no_file(qtbot, window, three):
    sidebar = window._sidebar
    item = sidebar.item(favourite_row(window, "beta"))

    data = sidebar.mimeData([item])

    assert bytes(data.data(sidebar_module.FAVOURITE_MIME)).decode() == three.items[1].id
    # Nothing that a folder or another app could take for a file
    assert not data.hasUrls()
    assert data.formats() == [sidebar_module.FAVOURITE_MIME]


def test_a_favourite_dropped_before_another_goes_before_it(qtbot, window, three):
    gamma = three.items[2]

    event = drag_favourite(window, gamma, favourite_row(window, "alpha"), upper=True)

    assert event.isAccepted()
    assert titles(three) == ["gamma", "alpha", "beta"]


def test_a_favourite_dropped_after_another_goes_after_it(qtbot, window, three):
    alpha = three.items[0]

    drag_favourite(window, alpha, favourite_row(window, "beta"), upper=False)

    assert titles(three) == ["beta", "alpha", "gamma"]


def test_dropped_where_it_already_is_changes_nothing(qtbot, window, three, places_file):
    before = places_file.read_text()
    beta = three.items[1]

    drag_favourite(window, beta, favourite_row(window, "gamma"), upper=True)
    drag_favourite(window, beta, favourite_row(window, "beta"), upper=True)
    drag_favourite(window, beta, favourite_row(window, "beta"), upper=False)

    assert titles(three) == ["alpha", "beta", "gamma"]
    assert places_file.read_text() == before


def test_dropped_on_the_heading_it_goes_first_and_at_the_end_it_goes_last(qtbot, window, three):
    gamma = three.items[2]

    drag_favourite(window, gamma, sidebar_names(window).index("▾ Favourites"), upper=False)
    assert titles(three) == ["gamma", "alpha", "beta"]

    # Alpha is second now: below beta it is last
    drag_favourite(window, three.items[1], favourite_row(window, "beta"), upper=False)
    assert titles(three) == ["gamma", "beta", "alpha"]


def test_dropped_on_a_place_it_is_refused_and_nothing_moves(qtbot, window, three, tree, jobs):
    event = drag_favourite(window, three.items[0], window._sidebar.row_of("Home"), upper=True)

    assert not event.isAccepted()
    assert titles(three) == ["alpha", "beta", "gamma"]
    assert jobs.jobs == []
    assert (tree / "alpha").exists()
    # And the folder stays where it is: the drag carried no file to move
    assert window._location.get_path() == str(tree)


def test_a_favourite_that_is_gone_is_not_moved(qtbot, window, three):
    from filehouse.qt_models.favourites import Favourite

    stale = Favourite("0/0", "gone", gfile("/nowhere"))

    drag_favourite(window, stale, favourite_row(window, "beta"), upper=True)

    assert titles(three) == ["alpha", "beta", "gamma"]


def test_the_insertion_line_shows_while_dragging_and_not_after(qtbot, window, three):
    from PySide6.QtCore import QMimeData
    from PySide6.QtGui import QDragLeaveEvent, QDragMoveEvent

    sidebar = window._sidebar
    mime = QMimeData()
    mime.setData(sidebar_module.FAVOURITE_MIME, three.items[0].id.encode())
    row = favourite_row(window, "gamma")
    rect = sidebar.visualItemRect(sidebar.item(row))

    sidebar.dragMoveEvent(
        QDragMoveEvent(
            rect.center(),
            Qt.DropAction.MoveAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )
    assert sidebar._drop_line is not None

    sidebar.dragLeaveEvent(QDragLeaveEvent())
    assert sidebar._drop_line is None
