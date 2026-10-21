"""Undo from the window: Ctrl+Z and the folder menu take back the last rename, move,
trash or new folder, of this window or another."""

import os
from datetime import datetime
from urllib.parse import unquote

import pytest
from PySide6.QtCore import Qt
from shiboken6 import isValid
from ui_helpers import TIMEOUT, gfile, off_the_ui_thread, settle

from filehouse.core.operations import JobKind, JobState, job, trash, undo
from filehouse.qt_models import job_model
from filehouse.ui.dialogs.details import ErrorDetails
from filehouse.ui.main_window import MainWindow


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "src" / "sub").mkdir(parents=True)
    (tmp_path / "dst").mkdir()
    (tmp_path / "src" / "a.txt").write_text("aaa")
    (tmp_path / "src" / "b.txt").write_text("bbb")
    return tmp_path


@pytest.fixture
def open_window(qtbot, jobs):
    windows = []

    def open_at(path):
        window = MainWindow(gfile(path), jobs=jobs)
        windows.append(window)
        window.show()
        qtbot.waitUntil(lambda: not window._model.loading, timeout=TIMEOUT)
        return window

    yield open_at
    jobs.cancel_all()
    qtbot.waitUntil(lambda: jobs.active_count() == 0, timeout=TIMEOUT)
    for window in windows:
        if isValid(window):
            window.close()


def names(path):
    return sorted(os.listdir(path))


def select(window, *wanted):
    window._view.select_where(lambda info: info.name in wanted)


def rename(window, old, new):
    select(window, old)
    dialog = window.rename()
    dialog.edit.setText(new)
    dialog.accept()
    settle(window)


def test_there_is_nothing_to_undo_at_first(tree, open_window):
    window = open_window(tree / "src")

    assert window._actions.undo.text() == "Undo"
    assert not window._actions.undo.isEnabled()
    assert window.undo() is None


def test_ctrl_z_takes_a_rename_back(qtbot, tree, open_window):
    window = open_window(tree / "src")
    rename(window, "a.txt", "notes.txt")
    assert window._actions.undo.isEnabled()
    assert window._actions.undo.text() == "Undo rename of “notes.txt”"
    # The offscreen platform gives no window back its focus when a dialog closes
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow, timeout=TIMEOUT)

    qtbot.keyClick(window._view._tree, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    settle(window)

    assert names(tree / "src") == ["a.txt", "b.txt", "sub"]
    assert window.statusBar().currentMessage() == "Undid the rename of “notes.txt”"
    assert not window._actions.undo.isEnabled() and window._actions.undo.text() == "Undo"
    # The file with its name back is the selected one
    qtbot.waitUntil(
        lambda: [info.name for info in window._view.selected_infos()] == ["a.txt"],
        timeout=TIMEOUT,
    )


def test_a_rename_is_taken_back_on_a_worker_and_not_the_window(qtbot, tree, open_window, stuck):
    window = open_window(tree / "src")
    rename(window, "a.txt", "notes.txt")
    back = stuck(undo, "rename_back")

    window.undo()
    qtbot.wait(50)

    assert names(tree / "src") == ["b.txt", "notes.txt", "sub"]
    back.release()
    settle(window)
    assert names(tree / "src") == ["a.txt", "b.txt", "sub"]
    assert window.statusBar().currentMessage() == "Undid the rename of “notes.txt”"
    assert off_the_ui_thread(back)


def test_a_rename_to_the_same_name_is_nothing_to_undo(tree, open_window):
    window = open_window(tree / "src")

    rename(window, "a.txt", "a.txt")

    assert not window._actions.undo.isEnabled()


def test_a_rename_is_not_taken_back_over_what_has_the_old_name_now(tree, open_window):
    window = open_window(tree / "src")
    rename(window, "a.txt", "notes.txt")
    (tree / "src" / "a.txt").write_text("made since")

    window.undo()
    settle(window)

    assert window.statusBar().currentMessage() == (
        "Could not undo the rename of “notes.txt”: notes.txt: already exists."
    )
    assert (tree / "src" / "a.txt").read_text() == "made since"
    assert (tree / "src" / "notes.txt").read_text() == "aaa"


def test_renaming_several_is_taken_back_as_one(qtbot, tree, open_window):
    window = open_window(tree / "src")
    select(window, "a.txt", "b.txt")
    dialog = window.rename()
    dialog.set_way("number")
    dialog.pattern.setText("note #")
    settle(window)
    dialog.accept()
    settle(window)
    assert names(tree / "src") == ["note 1.txt", "note 2.txt", "sub"]
    assert window._actions.undo.text() == "Undo rename of 2 items"

    window.undo()
    settle(window)

    assert names(tree / "src") == ["a.txt", "b.txt", "sub"]
    assert (tree / "src" / "a.txt").read_text() == "aaa"


def test_a_new_folder_is_removed_while_it_is_empty(tree, open_window):
    window = open_window(tree / "src")
    dialog = window.new_folder()
    dialog.edit.setText("Photos")
    dialog.accept()
    settle(window)
    assert window._actions.undo.text() == "Undo new “Photos”"

    window.undo()
    settle(window)

    assert names(tree / "src") == ["a.txt", "b.txt", "sub"]
    assert window.statusBar().currentMessage() == "Undid the new “Photos”"


def test_a_new_folder_that_was_used_since_stays(tree, open_window):
    window = open_window(tree / "src")
    dialog = window.new_folder()
    dialog.edit.setText("Photos")
    dialog.accept()
    settle(window)
    (tree / "src" / "Photos" / "holiday.jpg").write_text("x")

    window.undo()
    settle(window)

    assert (tree / "src" / "Photos" / "holiday.jpg").read_text() == "x"
    assert window.statusBar().currentMessage() == (
        "Could not undo the new “Photos”: Photos: is not empty any more."
    )


def test_a_new_file_is_removed_while_nothing_is_written_to_it(tree, open_window):
    window = open_window(tree / "src")
    dialog = window.new_file()
    dialog.edit.setText("todo.txt")
    dialog.accept()
    settle(window)
    assert (tree / "src" / "todo.txt").exists()

    window.undo()
    settle(window)

    assert names(tree / "src") == ["a.txt", "b.txt", "sub"]


def test_a_move_is_taken_back_by_a_job(qtbot, tree, open_window, jobs, wait_final):
    window = open_window(tree / "src")
    select(window, "a.txt", "sub")
    selected = window._selected_locations()
    wait_final(jobs, jobs.start(JobKind.MOVE, selected, gfile(tree / "dst"), origin=window))
    assert names(tree / "dst") == ["a.txt", "sub"]
    assert window._actions.undo.text() == "Undo move of 2 items"

    undone = window.undo()
    assert job_model.heading(undone.job) == "Moving 2 items back"
    back = wait_final(jobs, undone.job)

    assert back.kind is JobKind.MOVE and back.state is JobState.DONE
    assert names(tree / "src") == ["a.txt", "b.txt", "sub"]
    assert names(tree / "dst") == []
    # Taking back is not kept to be taken back: there is no redo
    assert not window._actions.undo.isEnabled()
    assert jobs.origin(back) is window


def test_a_copy_leaves_undo_as_it_was(tree, open_window, jobs, wait_final):
    window = open_window(tree / "src")
    rename(window, "b.txt", "c.txt")

    a = gfile(tree / "src" / "a.txt")
    wait_final(jobs, jobs.start(JobKind.COPY, [a], gfile(tree / "dst"), origin=window))

    assert window._actions.undo.text() == "Undo rename of “c.txt”"


def test_actions_are_taken_back_from_the_last_one(tree, open_window, jobs, wait_final):
    window = open_window(tree / "src")
    rename(window, "a.txt", "notes.txt")
    notes = gfile(tree / "src" / "notes.txt")
    wait_final(jobs, jobs.start(JobKind.MOVE, [notes], gfile(tree / "dst"), origin=window))

    wait_final(jobs, window.undo().job)
    assert names(tree / "src") == ["b.txt", "notes.txt", "sub"]
    assert window._actions.undo.text() == "Undo rename of “notes.txt”"

    window.undo()
    settle(window)
    assert names(tree / "src") == ["a.txt", "b.txt", "sub"]


def test_what_one_window_did_another_can_take_back(tree, open_window):
    first, second = open_window(tree / "src"), open_window(tree / "dst")
    rename(first, "a.txt", "notes.txt")

    assert second._actions.undo.isEnabled()
    assert second._actions.undo.text() == "Undo rename of “notes.txt”"
    second.undo()
    settle(second)

    assert names(tree / "src") == ["a.txt", "b.txt", "sub"]
    assert not first._actions.undo.isEnabled()


def test_undo_is_in_the_folders_menu(tree, open_window):
    window = open_window(tree / "src")

    assert window._actions.undo in window._menu_for(None).actions()


def test_several_that_could_not_be_taken_back_are_listed_by_name(qtbot, tree, open_window):
    window = open_window(tree / "src")
    select(window, "a.txt", "b.txt")
    dialog = window.rename()
    dialog.set_way("number")
    dialog.pattern.setText("note #")
    settle(window)
    dialog.accept()
    settle(window)
    (tree / "src" / "a.txt").write_text("made since")
    (tree / "src" / "b.txt").write_text("made since")

    window.undo()
    settle(window)

    details = window.findChild(ErrorDetails)
    assert details is not None
    assert window.statusBar().currentMessage() == (
        "Could not undo the rename of 2 items for 2 of them."
    )
    details.close()


# The trash: the real one of the tests' own home, read as the freedesktop trash writes it


@pytest.fixture
def listed_trash(monkeypatch):
    """Without GVFS there is no `trash:///` to list: what is in the trash of the
    tests' home is read from its own files, which say where each item was and when."""
    folder = os.path.join(os.environ["XDG_DATA_HOME"], "Trash")

    def listed(_cancellable):
        found = []
        info_dir = os.path.join(folder, "info")
        for name in os.listdir(info_dir) if os.path.isdir(info_dir) else []:
            lines = open(os.path.join(info_dir, name), encoding="utf-8").read().splitlines()
            fields = dict(line.split("=", 1) for line in lines if "=" in line)
            item = os.path.join(folder, "files", name.removesuffix(".trashinfo"))
            found.append(
                (
                    gfile(item),
                    unquote(fields["Path"]),
                    datetime.fromisoformat(fields["DeletionDate"]),
                )
            )
        return found

    monkeypatch.setattr(trash, "_list", listed)
    monkeypatch.setattr(job, "_TRASH_SETTLES_SECONDS", 0)


def test_a_trash_is_taken_back_by_a_restore(
    qtbot, home_dir, open_window, jobs, wait_final, listed_trash
):
    (home_dir / "old.txt").write_text("x")
    (home_dir / "keep.txt").write_text("k")
    window = open_window(home_dir)
    select(window, "old.txt")
    wait_final(jobs, window.trash_selected())
    assert names(home_dir) == ["keep.txt"]
    assert window._actions.undo.text() == "Undo trash of “old.txt”"

    back = wait_final(jobs, window.undo().job)

    assert back.kind is JobKind.RESTORE and back.state is JobState.DONE
    assert names(home_dir) == ["keep.txt", "old.txt"]
    assert (home_dir / "old.txt").read_text() == "x"
    assert not window._actions.undo.isEnabled()
