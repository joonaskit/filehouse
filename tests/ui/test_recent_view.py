"""The Recent place: a list of files used lately, which are the files themselves."""

import pytest
from gi.repository import Gio
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication
from ui_helpers import TIMEOUT, gfile

from filehouse.core import locations
from filehouse.core.listing import FileInfo, natural_key
from filehouse.core.operations import JobKind
from filehouse.integration import desktop
from filehouse.qt_models import directory_model
from filehouse.ui import clipboard
from filehouse.ui import main_window as main_window_module
from filehouse.ui.main_window import MainWindow


class FakeRecent:
    def __init__(self):
        self.added = []
        self.forgotten = []

    def add(self, location, content_type=None):
        self.added.append(location.get_basename())
        return True

    def clear(self, done=None):
        self.cleared = getattr(self, "cleared", 0) + 1
        if done is not None:
            done()

    def forget(self, locations_, done=None):
        self.forgotten.append([location.get_basename() for location in locations_])
        if done is not None:
            done()


def entry(name, target, used, is_dir=False, content_type="text/plain"):
    return FileInfo(
        name=name,
        display_name=target.get_basename(),
        is_dir=is_dir,
        is_hidden=False,
        is_symlink=False,
        size=5,
        modified=1_600_000_000,
        content_type=content_type,
        sort_key=natural_key(target.get_basename()),
        target_uri=target.get_uri(),
        used=used,
    )


@pytest.fixture
def places(tmp_path):
    folder = tmp_path / "docs"
    folder.mkdir()
    for name in ("old.txt", "new.txt", "report.pdf"):
        (folder / name).write_text("x")
    (folder / "sub").mkdir()
    return folder


@pytest.fixture
def window(qtbot, tmp_path, places, jobs, monkeypatch):
    """A window whose Recent lists three files, without asking the real list."""
    listed = [
        entry("id1", gfile(places / "old.txt"), used=1_000),
        entry("id2", gfile(places / "new.txt"), used=3_000),
        entry("id3", gfile(places / "report.pdf"), used=2_000, content_type="application/pdf"),
        entry("id4", gfile(places / "sub"), used=500, is_dir=True, content_type="inode/directory"),
    ]
    state = {"entries": listed}

    def fake_listing(location, on_batch, cancellable=None, batch_size=200):
        if locations.in_recent(location):
            on_batch(list(state["entries"]))
        else:
            real_listing(location, on_batch, cancellable, batch_size)

    real_listing = directory_model.list_directory
    monkeypatch.setattr(directory_model, "list_directory", fake_listing)
    opened = []
    monkeypatch.setattr(desktop, "open_default", lambda target, _error: opened.append(target))
    recent = FakeRecent()
    window = MainWindow(gfile(tmp_path), jobs=jobs, recent=recent)
    qtbot.addWidget(window)
    window.resize(1000, 600)
    window.show()
    qtbot.waitUntil(lambda: not window._model.loading, timeout=TIMEOUT)
    window.recent, window.opened, window.state = recent, opened, state
    return window


def go_recent(qtbot, window):
    window.navigate(locations.recent())
    qtbot.waitUntil(
        lambda: not window._model.loading and window._model.rowCount() > 0, timeout=TIMEOUT
    )


def names(window):
    return [window._proxy.index(row, 0).data() for row in range(window._proxy.rowCount())]


def test_recent_is_a_place_when_the_system_has_it():
    found = [place for place in locations.places() if place.name == "Recent"]

    assert bool(found) == locations.has_recent()
    if found:
        assert found[0].location.get_uri() == "recent:///"
        # Before the trash, which is the last
        assert locations.places()[-1].name == "Trash" or not locations.has_trash()


def test_its_name_and_way_in_the_path_bar():
    assert locations.display_name(locations.recent()) == "Recent"
    assert [name for name, _ in locations.segments(locations.recent())] == ["Recent"]


def test_an_entry_is_the_file_it_stands_for(places):
    info = entry("id1", gfile(places / "old.txt"), used=1)

    assert locations.entry_location(locations.recent(), info).get_path() == str(places / "old.txt")
    # Elsewhere an entry is just a name in the folder
    assert locations.entry_location(gfile(places), info).get_basename() == "id1"


def test_the_latest_comes_first_and_the_sort_comes_back(qtbot, window):
    window._view.sort(directory_model.SIZE, Qt.SortOrder.AscendingOrder)

    go_recent(qtbot, window)

    # Folders first, as everywhere, then the latest used
    assert names(window) == ["sub", "new.txt", "report.pdf", "old.txt"]
    assert window._view.sort_column() == directory_model.MODIFIED
    assert window.windowTitle() == "Recent — Files"

    window.go_back()
    qtbot.waitUntil(lambda: not window._model.loading, timeout=TIMEOUT)

    assert window._view.sort_column() == directory_model.SIZE
    assert window._view.sort_order() == Qt.SortOrder.AscendingOrder


def test_opening_an_entry_opens_the_file_and_tells_the_lists(qtbot, window, places):
    go_recent(qtbot, window)
    window._on_item_activated(window._model.info(window._model.row_for_name("id2")))

    assert [target.get_path() for target in window.opened] == [str(places / "new.txt")]
    assert window.recent.added == ["new.txt"]


def test_opening_a_folder_of_the_list_goes_there(qtbot, window, places):
    go_recent(qtbot, window)

    window._on_item_activated(window._model.info(window._model.row_for_name("id4")))

    assert window._location.get_path() == str(places / "sub")


def test_it_is_for_looking_at(qtbot, window):
    go_recent(qtbot, window)
    window._view.select_name("id2")

    assert window._copy.isEnabled()
    assert window._forget.isEnabled()
    for action in (
        window._cut,
        window._rename,
        window._move_to,
        window._trash,
        window._delete,
        window._paste,
        window._new_folder,
        window._new_file,
        window._permissions,
        window._move_other,
    ):
        assert not action.isEnabled(), action.text()


def test_it_does_not_take_the_files_away(qtbot, window, places, jobs):
    go_recent(qtbot, window)
    window._view.select_name("id2")

    assert window.delete_selected() is None
    assert window.rename() is None
    assert window.move_selected() is None
    assert window.new_folder() is None
    assert window.set_permissions(None) is None
    assert jobs.jobs == []
    assert (places / "new.txt").exists()
    # The Delete key's own action, whichever is asked, only takes the entry off the list
    window.trash_selected()
    assert jobs.jobs == []
    assert window.recent.forgotten == [["new.txt"]]
    assert (places / "new.txt").exists()


def test_delete_takes_the_entry_off_the_list_and_nothing_else(qtbot, window, places):
    go_recent(qtbot, window)
    window._view.select_name("id2")

    removed = window.remove_from_recent()

    assert [location.get_path() for location in removed] == [str(places / "new.txt")]
    assert window.recent.forgotten == [["new.txt"]]
    assert (places / "new.txt").exists()


def test_the_delete_key_is_the_lists_in_recent(qtbot, window, places):
    go_recent(qtbot, window)
    window._view.select_name("id1")

    qtbot.keyClick(window._view, Qt.Key.Key_Delete)

    assert window.recent.forgotten == [["old.txt"]]
    assert (places / "old.txt").exists()


def test_what_is_selected_is_the_files(qtbot, window, places):
    go_recent(qtbot, window)
    window._view.select_name("id3")

    assert [path.get_path() for path in window._selected_locations()] == [
        str(places / "report.pdf")
    ]

    window.copy()
    uris, cut = clipboard.get()
    assert uris == [gfile(places / "report.pdf").get_uri()] and not cut

    window.copy_path()
    assert QApplication.clipboard().text() == str(places / "report.pdf")


def test_show_in_folder_goes_to_the_file(qtbot, window, places):
    go_recent(qtbot, window)

    window.show_in_folder(window._model.info(window._model.row_for_name("id3")))

    assert window._location.get_path() == str(places)
    qtbot.waitUntil(lambda: not window._model.loading, timeout=TIMEOUT)
    qtbot.waitUntil(
        lambda: [info.name for info in window._view.selected_infos()] == ["report.pdf"],
        timeout=TIMEOUT,
    )


def test_the_menu_of_an_entry(qtbot, window):
    go_recent(qtbot, window)
    menu = window._menu_for(window._model.info(window._model.row_for_name("id3")))

    texts = [action.text() for action in menu.actions() if action.text()]

    assert texts[:1] == ["Open"]
    assert "Show in folder" in texts
    assert "Remove from recent files" in texts
    assert "Copy path" in texts
    assert "Move to trash" not in texts
    assert "Rename…" not in texts
    assert "Delete permanently…" not in texts


def test_the_menu_of_the_list_has_no_new_or_paste(qtbot, window):
    go_recent(qtbot, window)
    texts = [action.text() for action in window._menu_for(None).actions() if action.text()]

    assert "Select all" in texts
    assert "New folder…" not in texts
    assert "Paste" not in texts


def test_nothing_can_be_dropped_into_it(qtbot, window, places, jobs):
    go_recent(qtbot, window)
    uris = [gfile(places / "old.txt").get_uri()]

    assert window._on_dropped(uris, None, Qt.KeyboardModifier.NoModifier, QPoint(0, 0)) is None
    assert jobs.jobs == []


def test_a_file_can_be_copied_from_it_to_the_other_pane(
    qtbot, window, places, tmp_path, jobs, wait_final
):
    window.set_split(True)
    other = window._other_pane()
    qtbot.waitUntil(lambda: not other.model.loading, timeout=TIMEOUT)
    other.navigate(gfile(places))
    qtbot.waitUntil(lambda: not other.model.loading, timeout=TIMEOUT)
    target_folder = tmp_path / "elsewhere"
    target_folder.mkdir()
    other.navigate(gfile(target_folder))
    qtbot.waitUntil(lambda: not other.model.loading, timeout=TIMEOUT)
    go_recent(qtbot, window)
    window._view.select_name("id2")

    job = wait_final(jobs, window.copy_to_other_pane())

    assert job.kind is JobKind.COPY
    assert (target_folder / "new.txt").exists()
    assert (places / "new.txt").exists()
    # Moving the files out of a list of what was used is not offered
    assert window.move_to_other_pane() is None


def test_an_empty_list_says_so(qtbot, window):
    window.state["entries"] = []

    window.navigate(locations.recent())

    qtbot.waitUntil(lambda: not window._model.loading, timeout=TIMEOUT)
    qtbot.waitUntil(lambda: window._view._message.text() == "No recent files", timeout=TIMEOUT)


def test_a_listing_of_the_recent_files_is_read_into_entries():
    info = Gio.FileInfo()
    info.set_name("0123abcd")
    info.set_display_name("paper.pdf")
    info.set_file_type(Gio.FileType.REGULAR)
    info.set_size(10)
    info.set_is_symlink(False)
    info.set_attribute_string("standard::fast-content-type", "application/octet-stream")
    info.set_attribute_string("standard::target-uri", "file:///home/me/paper.pdf")
    info.set_attribute_uint64("time::modified", 1_600_000_000)
    info.set_attribute_int64("recent::modified", 1_700_000_000)

    entry_ = directory_model.info_from_gio(info)

    assert entry_.target_uri == "file:///home/me/paper.pdf"
    # The name says what the type is, as the list itself does not
    assert entry_.content_type == "application/pdf"
    assert entry_.modified == 1_600_000_000
    assert entry_.used == 1_700_000_000
    assert entry_.shown_date == 1_700_000_000


# Clearing the list


@pytest.fixture
def asked(monkeypatch):
    """What the question about clearing was, and how it is answered."""
    answers = {"clear": True, "asked": []}

    def ask(_parent, count):
        answers["asked"].append(count)
        return answers["clear"]

    monkeypatch.setattr(main_window_module, "ask_to_clear_recent", ask)
    return answers


def test_the_list_can_be_cleared_after_asking(qtbot, window, asked, places):
    go_recent(qtbot, window)

    assert window._clear_recent.isEnabled()
    assert window.clear_recent()

    assert asked["asked"] == [4]
    assert window.recent.cleared == 1
    # The files are the files' own business
    assert (places / "new.txt").exists()


def test_saying_no_clears_nothing(qtbot, window, asked):
    go_recent(qtbot, window)
    asked["clear"] = False

    assert not window.clear_recent()

    assert getattr(window.recent, "cleared", 0) == 0


def test_there_is_nothing_to_clear_elsewhere_or_in_an_empty_list(qtbot, window, asked):
    assert not window._clear_recent.isEnabled()
    assert not window.clear_recent()

    window.state["entries"] = []
    window.navigate(locations.recent())
    qtbot.waitUntil(lambda: not window._model.loading, timeout=TIMEOUT)
    window._update_state()

    assert not window._clear_recent.isEnabled()
    assert not window.clear_recent()
    assert asked["asked"] == []


def test_it_is_in_the_menu_of_the_list_and_nowhere_else(qtbot, window):
    go_recent(qtbot, window)

    assert window._clear_recent in window._menu_for(None).actions()
    assert window._clear_recent not in window._menu_for(window._model.info(0)).actions()
    window.go_back()
    qtbot.waitUntil(lambda: not window._model.loading, timeout=TIMEOUT)
    assert window._clear_recent not in window._menu_for(None).actions()
