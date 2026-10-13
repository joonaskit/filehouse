"""The columns of the details view: chosen, ordered, remembered, and cut down when narrow."""

import pytest
from PySide6.QtCore import Qt
from ui_helpers import TIMEOUT, gfile

from filehouse.qt_models import columns
from filehouse.qt_models.directory_model import (
    ACCESSED,
    CREATED,
    DELETED,
    GROUP,
    NAME,
    ORIGINAL,
    OWNER,
    SIZE,
    TYPE,
)
from filehouse.ui import settings
from filehouse.ui.pane import Pane


def shown(view):
    """The columns that show, left to right."""
    header = view._tree.header()
    return [
        columns.KEYS[header.logicalIndex(visual)]
        for visual in range(header.count())
        if not header.isSectionHidden(header.logicalIndex(visual))
    ]


@pytest.fixture
def pane(qtbot, tmp_path):
    (tmp_path / "a.txt").write_text("12345")
    (tmp_path / "b.pdf").write_text("%PDF")
    (tmp_path / "sub").mkdir()
    pane = Pane(show_hidden=False)
    qtbot.addWidget(pane.view)
    pane.view.resize(900, 400)
    pane.view.show()
    with qtbot.waitSignal(pane.model.loaded, timeout=TIMEOUT):
        pane.show(gfile(tmp_path))
    yield pane
    pane.close()


def test_the_keys_are_the_columns_of_the_model():
    assert columns.column("name") == NAME
    assert columns.column("size") == SIZE
    assert columns.column("deleted") == DELETED


def test_nothing_chosen_is_the_default_and_the_name_is_always_first():
    assert columns.choose(None, columns.FILES) == ["name", "permissions", "modified", "size"]
    assert columns.choose(["size", "name", "type"], columns.FILES) == ["name", "size", "type"]
    assert columns.choose([], columns.FILES) == ["name"]


def test_a_stored_choice_that_was_edited_by_hand_is_not_trusted():
    stored = ["type", "type", 7, None, "nonsense", "original", "size"]

    assert columns.choose(stored, columns.FILES) == ["name", "type", "size"]
    assert columns.choose("size", columns.FILES) == ["name", "permissions", "modified", "size"]


def test_the_trash_has_two_columns_of_its_own():
    assert "original" not in columns.offered(columns.FILES)
    assert "deleted" not in columns.offered(columns.FILES)
    assert {"original", "deleted"} <= set(columns.offered(columns.TRASH))
    assert columns.choose(None, columns.TRASH) == ["name", "original", "deleted", "size"]


def test_narrow_keeps_the_name_and_two_columns_that_are_not_dates():
    assert columns.narrow(["name", "permissions", "modified", "size"]) == [
        "name",
        "permissions",
        "size",
    ]
    assert columns.narrow(["name", "created", "type", "owner", "group"]) == [
        "name",
        "type",
        "owner",
    ]


def test_a_folder_shows_the_default_columns_at_first(pane):
    assert shown(pane.view) == ["name", "permissions", "modified", "size"]


def test_a_column_is_shown_and_hidden_from_the_menu_and_remembered(qtbot, pane):
    with qtbot.waitSignal(pane.view.columns_chosen) as chosen:
        pane.view._show_column("type", True)
    assert chosen.args == [["name", "permissions", "modified", "size", "type"]]
    assert shown(pane.view)[-1] == "type"
    assert settings.stored_columns(columns.FILES) == [
        "name",
        "permissions",
        "modified",
        "size",
        "type",
    ]

    pane.view._show_column("permissions", False)
    assert shown(pane.view) == ["name", "modified", "size", "type"]
    assert settings.stored_columns(columns.FILES) == ["name", "modified", "size", "type"]


def test_the_new_columns_say_what_they_are(qtbot, pane):
    proxy = pane.proxy
    row = next(r for r in range(proxy.rowCount()) if proxy.index(r, NAME).data() == "b.pdf")

    assert proxy.index(row, TYPE).data() == "PDF document"
    assert proxy.index(row, OWNER).data()
    assert proxy.index(row, GROUP).data()
    assert proxy.index(row, ACCESSED).data().startswith("Today, ")
    assert proxy.index(row, CREATED).data() in ("", proxy.index(row, ACCESSED).data())
    # What belongs to the trash says nothing elsewhere
    assert proxy.index(row, ORIGINAL).data() == ""
    assert proxy.index(row, DELETED).data() == ""


def test_the_list_is_sorted_by_a_column_that_was_added(qtbot, pane):
    pane.view._show_column("type", True)

    pane.view.sort(TYPE, Qt.SortOrder.AscendingOrder)

    # Folders stay on top; then by type: "PDF document" before "Plain text document"
    assert [pane.proxy.index(r, NAME).data() for r in range(3)] == ["sub", "b.pdf", "a.txt"]
    assert settings.view_for("file:///elsewhere")[0] == settings.DETAILS


def test_dragging_a_column_keeps_the_new_order(qtbot, pane):
    header = pane.view._tree.header()

    with qtbot.waitSignal(pane.view.columns_chosen) as chosen:
        header.moveSection(header.visualIndex(SIZE), 1)

    assert chosen.args == [["name", "size", "permissions", "modified"]]
    assert settings.stored_columns(columns.FILES) == ["name", "size", "permissions", "modified"]


def test_the_name_cannot_be_moved_from_the_first_place(pane):
    header = pane.view._tree.header()

    header.moveSection(header.visualIndex(SIZE), 0)

    assert shown(pane.view)[0] == "name"


def test_another_pane_follows_the_choice(qtbot, pane, tmp_path):
    other = Pane(show_hidden=False)
    qtbot.addWidget(other.view)
    with qtbot.waitSignal(other.model.loaded, timeout=TIMEOUT):
        other.show(gfile(tmp_path))

    pane.view._show_column("owner", True)

    assert shown(other.view)[-1] == "owner"
    other.close()


def test_default_columns_from_the_menu_forgets_the_choice(qtbot, pane):
    pane.view._show_column("type", True)

    pane.view.columns_chosen.emit([])

    assert settings.stored_columns(columns.FILES) is None
    assert shown(pane.view) == ["name", "permissions", "modified", "size"]


def test_a_narrow_window_shows_the_name_and_two_columns_and_keeps_the_choice(qtbot, pane):
    pane.view._show_column("created", True)

    pane.view.set_compact(True)
    assert shown(pane.view) == ["name", "permissions", "size"]
    # Not dragged meanwhile: what is saved is the whole choice, not what fits
    assert not pane.view._tree.header().sectionsMovable()

    pane.view.set_compact(False)
    assert shown(pane.view) == ["name", "permissions", "modified", "size", "created"]
    assert settings.stored_columns(columns.FILES) == [
        "name",
        "permissions",
        "modified",
        "size",
        "created",
    ]


def test_a_search_shows_the_folder_column_even_when_it_was_put_away(qtbot, pane):
    pane.view._show_column("permissions", False)
    assert "permissions" not in shown(pane.view)

    pane.model.set_search("a")
    assert "permissions" in shown(pane.view)
    assert (
        pane.model.headerData(columns.column("permissions"), Qt.Orientation.Horizontal) == "Folder"
    )

    # It was put there by the search: a column moved now doesn't make it a choice
    pane.view._show_column("type", True)
    assert "permissions" not in settings.stored_columns(columns.FILES)

    pane.model.set_search("")
    assert "permissions" not in shown(pane.view)


def test_the_trash_columns_are_chosen_apart_from_the_others(qtbot, pane):
    pane.view._show_column("type", True)
    stored = settings.stored_columns(columns.FILES)

    assert settings.stored_columns(columns.TRASH) is None
    pane.view.set_columns(columns.offered(columns.TRASH), columns.choose(None, columns.TRASH))
    assert shown(pane.view) == ["name", "original", "deleted", "size"]
    assert settings.stored_columns(columns.FILES) == stored


def test_a_sort_by_a_column_of_the_trash_is_the_name_elsewhere(qtbot, pane):
    settings.set_sort("deleted", True)

    assert pane.view.sort_column() == NAME


def test_a_pane_in_the_trash_shows_the_columns_chosen_for_it(qtbot, pane, monkeypatch):
    monkeypatch.setattr(Pane, "in_trash", property(lambda self: True))
    settings.set_columns(columns.TRASH, ["name", "deleted", "type"])

    assert shown(pane.view) == ["name", "deleted", "type"]

    # And a sort chosen by when it was deleted is kept there, not forced to the name
    settings.set_sort("deleted", True)
    assert pane.view.sort_column() == DELETED
