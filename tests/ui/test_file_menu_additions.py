"""Duplicate, Create link, renaming several at once and selecting: from the window."""

import os

import pytest
from PySide6.QtCore import QPoint, Qt
from shiboken6 import isValid
from ui_helpers import TIMEOUT, gfile, settle

from filehouse.core.operations import JobKind, JobState
from filehouse.ui.dialogs import rename_many as rename_dialog
from filehouse.ui.main_window import MainWindow


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "dst").mkdir()
    for name in ("IMG_1.jpg", "IMG_2.jpg", "notes.txt", "Holiday 2024.JPG"):
        (tmp_path / "src" / name).write_text(name)
    (tmp_path / "src" / "album").mkdir()
    (tmp_path / "src" / "album" / "inside.jpg").write_text("in")
    return tmp_path


@pytest.fixture
def window(qtbot, tree, jobs):
    window = MainWindow(gfile(tree / "src"), jobs=jobs)
    window.show()
    wait_loaded(qtbot, window)
    yield window
    jobs.cancel_all()
    qtbot.waitUntil(lambda: jobs.active_count() == 0, timeout=TIMEOUT)
    if isValid(window):
        window.close()


def wait_loaded(qtbot, window):
    qtbot.waitUntil(lambda: not window._model.loading, timeout=TIMEOUT)


def select(window, *names):
    wanted = set(names)
    window._view.select_where(lambda info: info.name in wanted)


def selected(window):
    return sorted(info.name for info in window._view.selected_infos())


def names(path):
    return sorted(os.listdir(path))


def menu_texts(window, info):
    return [action.text() for action in window._menu_for(info).actions()]


def info_of(window, name):
    return window._model.info(window._model.row_for_name(name))


# Duplicate


def test_duplicate_is_a_job_that_copies_beside(qtbot, tree, window, jobs, wait_final):
    select(window, "notes.txt", "album")

    job = wait_final(jobs, window.duplicate())

    assert job.kind is JobKind.DUPLICATE and job.state is JobState.DONE
    assert job.summary == "Duplicated 2 files"
    assert names(tree / "src") == [
        "Holiday 2024.JPG",
        "IMG_1.jpg",
        "IMG_2.jpg",
        "album",
        "album (2)",
        "notes (2).txt",
        "notes.txt",
    ]
    assert names(tree / "src" / "album (2)") == ["inside.jpg"]


def test_duplicate_and_link_are_in_the_menu_of_a_file_and_follow_the_selection(window):
    assert not window._actions.duplicate.isEnabled() and not window._actions.link.isEnabled()

    select(window, "notes.txt")

    assert window._actions.duplicate.isEnabled() and window._actions.link.isEnabled()
    texts = menu_texts(window, info_of(window, "notes.txt"))
    assert texts[texts.index("Rename…") :][:4] == [
        "Rename…",
        "Duplicate",
        "Create link",
        "Move to…",
    ]


def test_the_job_says_what_it_does(window, jobs):
    from filehouse.qt_models import job_model

    select(window, "notes.txt")
    job = window.duplicate()

    assert job_model.heading(job) == "Duplicating “notes.txt”"
    assert job_model.ICONS[job.kind] == "edit-duplicate"


# Links


def test_create_link_makes_one_beside_each_and_selects_it(qtbot, tree, window):
    select(window, "notes.txt")

    window.create_link()
    settle(window)

    assert os.readlink(tree / "src" / "Link to notes.txt") == "notes.txt"
    qtbot.waitUntil(lambda: selected(window) == ["Link to notes.txt"], timeout=TIMEOUT)
    assert window.statusBar().currentMessage() == "Link created"


def test_a_drop_with_ctrl_and_shift_links(qtbot, tree, window):
    window.navigate(gfile(tree / "dst"))
    wait_loaded(qtbot, window)
    keys = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
    dropped = [gfile(tree / "src" / name).get_uri() for name in ("notes.txt", "album")]

    window._on_dropped(dropped, None, keys, QPoint(0, 0))
    settle(window)

    assert os.readlink(tree / "dst" / "notes.txt") == str(tree / "src" / "notes.txt")
    assert os.readlink(tree / "dst" / "album") == str(tree / "src" / "album")
    # Nothing was moved or copied
    assert len(names(tree / "src")) == 5
    assert window.statusBar().currentMessage() == "2 links created"


def test_the_drop_menu_can_link(qtbot, tree, window, monkeypatch):
    from filehouse.ui import file_actions

    window.navigate(gfile(tree / "dst"))
    wait_loaded(qtbot, window)
    monkeypatch.setattr(window._file_actions, "ask_drop_action", lambda position: file_actions.LINK)

    window._on_dropped(
        [gfile(tree / "src" / "notes.txt").get_uri()],
        None,
        Qt.KeyboardModifier.AltModifier,
        QPoint(0, 0),
    )
    settle(window)

    assert os.path.islink(tree / "dst" / "notes.txt")


def test_a_link_that_cannot_be_made_is_said(qtbot, tree, window):
    window.navigate(gfile(tree / "dst"))
    wait_loaded(qtbot, window)
    keys = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier

    window._on_dropped(["trash:///gone.txt"], None, keys, QPoint(0, 0))
    settle(window)

    assert not os.listdir(tree / "dst")
    assert window.statusBar().currentMessage() == (
        "Could not create a link to gone.txt: a link can only point to this computer."
    )


# Renaming several


def test_f2_with_several_selected_opens_the_dialog_with_a_preview(window):
    select(window, "IMG_1.jpg", "IMG_2.jpg")

    dialog = window.rename()
    settle(window)

    assert isinstance(dialog, rename_dialog.RenameManyDialog)
    assert dialog.windowTitle() == "Rename 2 items"
    # Nothing typed yet: nothing to do
    assert not dialog._ok.isEnabled()
    assert dialog.status.text() == "No name changes."
    assert dialog.pattern.text() == "IMG #"
    dialog.reject()


def test_find_and_replace_renames_all_and_selects_them(qtbot, tree, window):
    select(window, "IMG_1.jpg", "IMG_2.jpg")
    dialog = window.rename()

    dialog.find.setText("IMG_")
    dialog.replacement.setText("Trip ")
    settle(window)

    assert [dialog.preview.topLevelItem(row).text(1) for row in range(2)] == [
        "Trip 1.jpg",
        "Trip 2.jpg",
    ]
    assert dialog.status.text() == "2 items get new names."
    finished = []
    dialog.finished.connect(finished.append)
    dialog.accept()
    settle(window)

    assert finished == [1]
    assert names(tree / "src") == [
        "Holiday 2024.JPG",
        "Trip 1.jpg",
        "Trip 2.jpg",
        "album",
        "notes.txt",
    ]
    qtbot.waitUntil(lambda: selected(window) == ["Trip 1.jpg", "Trip 2.jpg"], timeout=TIMEOUT)
    assert window.statusBar().currentMessage() == "2 items renamed"


def test_numbering_follows_the_order_of_the_list(tree, window):
    window._view.sort(0, Qt.SortOrder.DescendingOrder)
    select(window, "IMG_1.jpg", "IMG_2.jpg", "notes.txt")
    dialog = window.rename()

    dialog.set_way(rename_dialog.NUMBER)
    dialog.pattern.setText("Shot ##")
    dialog.start.setValue(7)
    settle(window)
    dialog.accept()
    settle(window)

    # Z first: notes, then IMG_2, then IMG_1
    assert (tree / "src" / "Shot 07.txt").read_text() == "notes.txt"
    assert (tree / "src" / "Shot 08.jpg").read_text() == "IMG_2.jpg"
    assert (tree / "src" / "Shot 09.jpg").read_text() == "IMG_1.jpg"


def test_names_that_collide_are_shown_and_block(tree, window):
    select(window, "IMG_1.jpg", "IMG_2.jpg")
    dialog = window.rename()
    before = names(tree / "src")

    dialog.set_way(rename_dialog.NUMBER)
    dialog.pattern.setText("notes")
    dialog.extension.setChecked(True)
    settle(window)
    # "notes 1" and "notes 2" are free; a name without a number is not asked for here
    assert dialog._ok.isEnabled()

    dialog.set_way(rename_dialog.REPLACE)
    dialog.find.setText("_1")
    dialog.replacement.setText("_2")
    dialog.extension.setChecked(False)
    settle(window)

    assert not dialog._ok.isEnabled()
    assert dialog.status.text() == "“IMG_2.jpg”: already exists. Nothing is renamed."
    assert dialog.preview.topLevelItem(0).toolTip(1) == "Already exists"
    dialog.accept()
    settle(window)
    assert dialog.result() == 0
    assert names(tree / "src") == before
    dialog.reject()


def test_a_rename_that_fails_says_why_and_leaves_all_as_it_was(tree, window):
    select(window, "IMG_1.jpg", "IMG_2.jpg")
    dialog = window.rename()
    dialog.find.setText("IMG_")
    dialog.replacement.setText("Trip ")
    settle(window)
    # After the preview was made
    (tree / "src" / "Trip 2.jpg").write_text("somebody else's")

    dialog.accept()
    settle(window)

    assert dialog.result() == 0
    assert dialog.status.text() == (
        "Could not rename “IMG_2.jpg”: already exists. Nothing is renamed."
    )
    assert (tree / "src" / "IMG_1.jpg").read_text() == "IMG_1.jpg"
    assert (tree / "src" / "Trip 2.jpg").read_text() == "somebody else's"
    dialog.reject()


def test_case_and_the_extension(tree, window):
    select(window, "Holiday 2024.JPG", "notes.txt")
    dialog = window.rename()

    dialog.set_way(rename_dialog.LOWER)
    settle(window)
    assert dialog.renames[0].name == "holiday 2024.JPG"
    dialog.extension.setChecked(True)
    settle(window)
    assert dialog.renames[0].name == "holiday 2024.jpg"
    # One of the two is lower case already: it stays, and is not in the way
    assert [rename.changes for rename in dialog.renames] == [True, False]
    dialog.accept()
    settle(window)

    assert "holiday 2024.jpg" in names(tree / "src")


# Selecting


def test_select_matching_takes_a_pattern_or_words(qtbot, window):
    dialog = window.select_matching()
    assert not dialog.buttons.button(dialog.buttons.StandardButton.Ok).isEnabled()

    dialog.edit.setText("*.jpg")
    assert dialog.found.text() == "3 items match"
    dialog.accept()

    assert selected(window) == ["Holiday 2024.JPG", "IMG_1.jpg", "IMG_2.jpg"]

    dialog = window.select_matching()
    dialog.edit.setText("2024 holiday")
    assert dialog.found.text() == "1 item matches"
    dialog.accept()

    assert selected(window) == ["Holiday 2024.JPG"]


def test_select_matching_can_add_to_what_is_selected(window):
    select(window, "album")
    dialog = window.select_matching()
    dialog.edit.setText("*.txt")
    dialog.add.setChecked(True)
    dialog.accept()

    assert selected(window) == ["album", "notes.txt"]


def test_nothing_matching_cannot_be_chosen(window):
    select(window, "album")
    dialog = window.select_matching()
    dialog.edit.setText("*.png")

    assert dialog.found.text() == "0 items match"
    assert not dialog.buttons.button(dialog.buttons.StandardButton.Ok).isEnabled()
    dialog.reject()
    assert selected(window) == ["album"]


def test_invert_selection(window):
    select(window, "album", "notes.txt")

    window._actions.invert.trigger()

    assert selected(window) == ["Holiday 2024.JPG", "IMG_1.jpg", "IMG_2.jpg"]
    window._actions.invert.trigger()
    assert selected(window) == ["album", "notes.txt"]


def test_inverting_nothing_selects_all_and_the_other_way_round(window):
    window._actions.invert.trigger()
    assert len(selected(window)) == 5

    window._actions.invert.trigger()
    assert selected(window) == []


def test_invert_works_in_the_icons_too(window):
    window._actions.icons_mode.trigger()
    select(window, "album")

    window.invert_selection()

    assert "album" not in selected(window) and len(selected(window)) == 4


def test_select_all_of_a_type(qtbot, tree, window):
    (tree / "src" / "more").mkdir()
    qtbot.waitUntil(lambda: window._model.row_for_name("more") >= 0, timeout=TIMEOUT)

    assert window.select_same_type(info_of(window, "IMG_1.jpg")) == 3
    assert selected(window) == ["Holiday 2024.JPG", "IMG_1.jpg", "IMG_2.jpg"]

    # Every folder, for a folder
    window.select_same_type(info_of(window, "album"))
    assert selected(window) == ["album", "more"]


def test_the_menus_have_the_selecting_entries(window):
    folder = menu_texts(window, None)
    assert folder[folder.index("Select all") :][:3] == [
        "Select all",
        "Select matching…",
        "Invert selection",
    ]
    assert "Select all of this type" in menu_texts(window, info_of(window, "notes.txt"))
    assert window._actions.select_matching.shortcut().toString() == "Ctrl+S"
    assert window._actions.invert.shortcut().toString() == "Ctrl+Shift+A"


def test_only_what_is_shown_is_selected(qtbot, tree, window):
    (tree / "src" / ".hidden.jpg").write_text("h")
    qtbot.waitUntil(lambda: window._model.row_for_name(".hidden.jpg") >= 0, timeout=TIMEOUT)

    dialog = window.select_matching()
    dialog.edit.setText("*.jpg")
    assert dialog.found.text() == "3 items match"
    dialog.accept()

    assert ".hidden.jpg" not in selected(window)
