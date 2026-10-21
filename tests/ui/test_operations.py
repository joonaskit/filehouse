"""File operations from the window: clipboard, drops, delete, rename, new folder, the panel."""

import dataclasses
import os

import pytest
from PySide6.QtCore import QPoint, Qt, QUrl
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid
from ui_helpers import TIMEOUT, gfile, off_the_ui_thread, settle, started_by

from filehouse.core import listing, locations
from filehouse.core.operations import (
    Answer,
    ConfirmDelete,
    Conflict,
    Failure,
    JobKind,
    JobState,
    permissions,
    quick,
)
from filehouse.qt_models import directory_model, job_model
from filehouse.ui import clipboard, main_window, settings
from filehouse.ui.dialogs.details import ErrorDetails
from filehouse.ui.main_window import MainWindow

NO_KEYS = Qt.KeyboardModifier.NoModifier
needs_permissions = pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permissions")


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "dst").mkdir()
    (tmp_path / "src" / "a.txt").write_text("aaa")
    (tmp_path / "src" / "b.txt").write_text("bbb")
    (tmp_path / "src" / "sub").mkdir()
    return tmp_path


@pytest.fixture
def open_window(qtbot, jobs):
    # Kept until the test is over, as the app keeps its windows
    windows = []

    def open_at(path):
        window = MainWindow(gfile(path), jobs=jobs)
        windows.append(window)
        window.show()
        wait_loaded(qtbot, window)
        return window

    yield open_at
    # With nothing running, closing asks nothing
    jobs.cancel_all()
    qtbot.waitUntil(lambda: jobs.active_count() == 0, timeout=TIMEOUT)
    for window in windows:
        # A window closed by its test is gone already
        if isValid(window):
            window.close()


def wait_loaded(qtbot, window):
    qtbot.waitUntil(lambda: not window._model.loading, timeout=TIMEOUT)


def go(qtbot, window, path):
    window.navigate(gfile(path))
    wait_loaded(qtbot, window)


def select(window, *names):
    window._view.selection_model().clearSelection()
    for name in names:
        row = window._model.row_for_name(name)
        index = window._proxy.mapFromSource(window._model.index(row, 0))
        flags = window._view.selection_model().SelectionFlag
        window._view.selection_model().select(index, flags.Select | flags.Rows)


def listed(window):
    return sorted(window._model.info(row).name for row in range(window._model.rowCount()))


def names(path):
    return sorted(os.listdir(path))


def uris(*paths):
    return [gfile(path).get_uri() for path in paths]


# Clipboard


def test_actions_follow_the_selection_and_the_clipboard(qtbot, tree, open_window):
    clipboard.clear()
    window = open_window(tree / "src")
    edits = (window._copy, window._cut, window._trash, window._delete)
    assert not any(action.isEnabled() for action in (*edits, window._rename, window._paste))

    select(window, "a.txt")
    assert all(action.isEnabled() for action in (*edits, window._rename))

    # Several can be renamed too, in a dialog of their own
    select(window, "a.txt", "b.txt")
    assert all(action.isEnabled() for action in (*edits, window._rename))

    window._copy.trigger()
    assert window._paste.isEnabled()


def test_copy_and_cut_put_uris_on_the_clipboard_as_dolphin_does(qtbot, tree, open_window):
    window = open_window(tree / "src")
    select(window, "a.txt", "sub")

    window._copy.trigger()
    data = QApplication.clipboard().mimeData()
    assert data.hasFormat("text/uri-list")
    assert sorted(url.toLocalFile() for url in data.urls()) == [
        str(tree / "src" / "a.txt"),
        str(tree / "src" / "sub"),
    ]
    assert not data.hasFormat(clipboard.CUT_MIME)

    window._cut.trigger()
    data = QApplication.clipboard().mimeData()
    assert bytes(data.data(clipboard.CUT_MIME)) == b"1"
    assert len(data.urls()) == 2


def test_copy_and_paste(qtbot, tree, open_window, jobs, wait_final):
    window = open_window(tree / "src")
    select(window, "a.txt", "b.txt")
    window._copy.trigger()
    go(qtbot, window, tree / "dst")

    job = wait_final(jobs, window.paste())

    assert job.kind is JobKind.COPY and job.state is JobState.DONE
    assert names(tree / "dst") == ["a.txt", "b.txt"]
    assert names(tree / "src") == ["a.txt", "b.txt", "sub"]
    # The view follows the folder without a reload
    qtbot.waitUntil(lambda: listed(window) == ["a.txt", "b.txt"], timeout=TIMEOUT)
    # What was copied can be pasted again
    assert window._paste.isEnabled()


def test_cut_and_paste_moves_and_uses_up_the_clipboard(qtbot, tree, open_window, jobs, wait_final):
    window = open_window(tree / "src")
    select(window, "a.txt")
    window._cut.trigger()
    go(qtbot, window, tree / "dst")

    job = wait_final(jobs, window.paste())

    assert job.kind is JobKind.MOVE and job.state is JobState.DONE
    assert names(tree / "dst") == ["a.txt"]
    assert names(tree / "src") == ["b.txt", "sub"]
    assert clipboard.get() == ([], False)
    assert window.paste() is None


def test_cut_and_paste_in_the_same_folder_does_nothing(qtbot, tree, open_window, jobs):
    window = open_window(tree / "src")
    select(window, "a.txt")
    window._cut.trigger()

    assert window.paste() is None
    assert jobs.jobs == []
    assert names(tree / "src") == ["a.txt", "b.txt", "sub"]


def test_paste_of_files_copied_in_another_app(qtbot, tree, open_window, jobs, wait_final):
    window = open_window(tree / "dst")
    clipboard.put(uris(tree / "src" / "b.txt"), cut=False)

    wait_final(jobs, window.paste())

    assert (tree / "dst" / "b.txt").read_text() == "bbb"


# Questions


def test_a_conflict_asks_and_keep_both_keeps_both(
    qtbot, tree, open_window, jobs, wait_final, wait_question
):
    (tree / "dst" / "a.txt").write_text("old")
    window = open_window(tree / "dst")
    clipboard.put(uris(tree / "src" / "a.txt"), cut=False)

    job = window.paste()
    box = wait_question(jobs)

    assert isinstance(box.question, Conflict)
    assert box.parent() is window
    assert box.text() == "“a.txt” already exists in dst."
    assert job.state is JobState.WAITING
    assert window._panel.row(job).text.text() == "Waiting for your answer"

    box.button_for(Answer.KEEP_BOTH).click()
    wait_final(jobs, job)

    assert jobs.prompter.current is None
    assert (tree / "dst" / "a.txt").read_text() == "old"
    assert (tree / "dst" / "a (2).txt").read_text() == "aaa"


def test_replace_is_not_offered_between_a_file_and_a_folder(
    qtbot, tree, open_window, jobs, wait_final, wait_question
):
    (tree / "dst" / "sub").write_text("a file")
    window = open_window(tree / "dst")
    clipboard.put(uris(tree / "src" / "sub"), cut=False)

    job = window.paste()
    box = wait_question(jobs)

    with pytest.raises(StopIteration):
        box.button_for(Answer.REPLACE)
    assert "cannot be replaced" in box.informativeText()
    box.button_for(Answer.SKIP).click()
    wait_final(jobs, job)
    assert (tree / "dst" / "sub").read_text() == "a file"


def test_apply_to_all_asks_once(qtbot, tree, open_window, jobs, wait_final, wait_question):
    for name in ("a.txt", "b.txt"):
        (tree / "dst" / name).write_text("old")
    window = open_window(tree / "dst")
    clipboard.put(uris(tree / "src" / "a.txt", tree / "src" / "b.txt"), cut=False)

    job = window.paste()
    box = wait_question(jobs)
    box.checkBox().setChecked(True)
    box.button_for(Answer.REPLACE).click()
    wait_final(jobs, job)

    assert job.state is JobState.DONE
    assert (tree / "dst" / "a.txt").read_text() == "aaa"
    assert (tree / "dst" / "b.txt").read_text() == "bbb"


def test_closing_the_question_cancels_the_job(
    qtbot, tree, open_window, jobs, wait_final, wait_question
):
    (tree / "dst" / "a.txt").write_text("old")
    window = open_window(tree / "dst")
    clipboard.put(uris(tree / "src" / "a.txt"), cut=False)

    job = window.paste()
    wait_question(jobs).reject()
    wait_final(jobs, job)

    assert job.state is JobState.CANCELLED
    assert (tree / "dst" / "a.txt").read_text() == "old"


def test_two_jobs_ask_one_at_a_time(qtbot, tree, open_window, jobs, wait_final, wait_question):
    for name in ("a.txt", "b.txt"):
        (tree / "dst" / name).write_text("old")
    window = open_window(tree / "dst")
    first = jobs.start(JobKind.COPY, [gfile(tree / "src" / "a.txt")], gfile(tree / "dst"), window)
    second = jobs.start(JobKind.COPY, [gfile(tree / "src" / "b.txt")], gfile(tree / "dst"), window)
    qtbot.waitUntil(
        lambda: first.state is JobState.WAITING and second.state is JobState.WAITING,
        timeout=TIMEOUT,
    )

    box = wait_question(jobs)
    asked_first = box.question.job
    box.button_for(Answer.REPLACE).click()
    qtbot.waitUntil(lambda: jobs.prompter.current not in (None, box), timeout=TIMEOUT)
    other = jobs.prompter.current
    assert {asked_first, other.question.job} == {first, second}
    other.button_for(Answer.REPLACE).click()

    wait_final(jobs, first)
    wait_final(jobs, second)
    assert (tree / "dst" / "a.txt").read_text() == "aaa"
    assert (tree / "dst" / "b.txt").read_text() == "bbb"


# Trash and delete


def test_delete_key_moves_to_the_trash(qtbot, home_dir, open_window, jobs, wait_final):
    (home_dir / "old.txt").write_text("x")
    (home_dir / "keep.txt").write_text("k")
    window = open_window(home_dir)
    select(window, "old.txt")

    qtbot.keyClick(window._view._tree, Qt.Key.Key_Delete)
    qtbot.waitUntil(lambda: len(jobs.jobs) == 1, timeout=TIMEOUT)
    job = wait_final(jobs, jobs.jobs[0])

    assert job.kind is JobKind.TRASH and job.state is JobState.DONE
    assert names(home_dir) == ["keep.txt"]
    trashed = os.listdir(os.path.join(os.environ["XDG_DATA_HOME"], "Trash", "files"))
    assert any(name.startswith("old") for name in trashed)


def test_permanent_delete_asks_first(qtbot, tree, open_window, jobs, wait_final, wait_question):
    window = open_window(tree / "src")
    select(window, "a.txt", "sub")

    job = window.delete_selected()
    box = wait_question(jobs)

    assert job.kind is JobKind.DELETE
    assert isinstance(box.question, ConfirmDelete)
    assert box.text() == "Permanently delete 2 items?"
    assert names(tree / "src") == ["a.txt", "b.txt", "sub"]

    box.button_for(Answer.CANCEL).click()
    wait_final(jobs, job)
    assert job.state is JobState.CANCELLED
    assert names(tree / "src") == ["a.txt", "b.txt", "sub"]


def test_confirmed_delete_removes_the_selection(
    qtbot, tree, open_window, jobs, wait_final, wait_question
):
    window = open_window(tree / "src")
    select(window, "a.txt", "sub")

    qtbot.keyClick(window._view._tree, Qt.Key.Key_Delete, Qt.KeyboardModifier.ShiftModifier)
    box = wait_question(jobs)
    box.button_for(Answer.DELETE).click()
    wait_final(jobs, jobs.jobs[0])

    assert names(tree / "src") == ["b.txt"]
    qtbot.waitUntil(lambda: listed(window) == ["b.txt"], timeout=TIMEOUT)


# Rename and new folder


def test_rename(qtbot, tree, open_window):
    window = open_window(tree / "src")
    select(window, "a.txt")

    dialog = window.rename()
    # The name is selected up to the extension, ready to be typed over
    assert dialog.edit.text() == "a.txt"
    assert dialog.edit.selectedText() == "a"
    dialog.edit.setText("notes.txt")
    dialog.accept()
    settle(window)

    assert names(tree / "src") == ["b.txt", "notes.txt", "sub"]
    qtbot.waitUntil(lambda: "notes.txt" in listed(window), timeout=TIMEOUT)
    qtbot.waitUntil(
        lambda: [info.name for info in window._view.selected_infos()] == ["notes.txt"],
        timeout=TIMEOUT,
    )


def test_rename_onto_an_existing_name_says_why_and_stays_open(qtbot, tree, open_window):
    window = open_window(tree / "src")
    select(window, "a.txt")

    dialog = window.rename()
    dialog.edit.setText("b.txt")
    dialog.accept()
    settle(window)

    assert dialog.result() == 0
    assert dialog.error.text() == "Could not rename “a.txt”: already exists."
    assert (tree / "src" / "a.txt").read_text() == "aaa"
    assert (tree / "src" / "b.txt").read_text() == "bbb"

    finished = []
    dialog.finished.connect(finished.append)
    dialog.edit.setText("c.txt")
    dialog.accept()
    settle(window)
    assert finished == [1]
    assert names(tree / "src") == ["b.txt", "c.txt", "sub"]


def test_new_folder(qtbot, tree, open_window):
    (tree / "src" / "New folder").mkdir()
    window = open_window(tree / "src")

    dialog = window.new_folder()
    settle(window)
    assert dialog.edit.text() == "New folder (2)"
    dialog.edit.setText("Photos")
    dialog.accept()
    settle(window)

    assert (tree / "src" / "Photos").is_dir()
    qtbot.waitUntil(
        lambda: [info.name for info in window._view.selected_infos()] == ["Photos"],
        timeout=TIMEOUT,
    )


def test_new_folder_with_a_taken_name_says_why(qtbot, tree, open_window):
    window = open_window(tree / "src")

    dialog = window.new_folder()
    dialog.edit.setText("sub")
    dialog.accept()
    settle(window)

    assert dialog.error.text() == "Could not create the folder: already exists."
    assert dialog.result() == 0


def test_new_file(qtbot, tree, open_window):
    (tree / "src" / "New file").write_text("")
    window = open_window(tree / "src")

    dialog = window.new_file()
    settle(window)
    assert dialog.edit.text() == "New file (2)"
    dialog.edit.setText("notes.md")
    dialog.accept()
    settle(window)

    assert (tree / "src" / "notes.md").read_text() == ""
    qtbot.waitUntil(
        lambda: [info.name for info in window._view.selected_infos()] == ["notes.md"],
        timeout=TIMEOUT,
    )


def test_new_file_with_a_taken_name_says_why_and_replaces_nothing(qtbot, tree, open_window):
    window = open_window(tree / "src")

    dialog = window.new_file()
    dialog.edit.setText("a.txt")
    dialog.accept()
    settle(window)

    assert dialog.error.text() == "Could not create the file: already exists."
    assert dialog.result() == 0
    assert (tree / "src" / "a.txt").read_text() == "aaa"


# On a folder that does not answer: the window goes on meanwhile


def test_a_new_folder_waits_for_its_folder_and_not_the_window(qtbot, tree, open_window, stuck):
    window = open_window(tree / "src")
    create = stuck(quick, "create_folder")
    dialog = window.new_folder()
    settle(window)
    dialog.edit.setText("Photos")

    dialog.accept()
    qtbot.wait(50)

    assert dialog.busy and not dialog.edit.isEnabled()
    assert not (tree / "src" / "Photos").exists()
    create.release()
    settle(window)
    assert (tree / "src" / "Photos").is_dir()
    assert off_the_ui_thread(create)


def test_a_folder_made_after_its_dialog_closed_can_still_be_taken_back(
    qtbot, tree, open_window, stuck
):
    window = open_window(tree / "src")
    create = stuck(quick, "create_folder")
    dialog = window.new_folder()
    settle(window)
    dialog.edit.setText("Photos")
    dialog.accept()

    dialog.reject()
    create.release()
    settle(window)

    assert (tree / "src" / "Photos").is_dir()
    assert window._undo.text() == "Undo new “Photos”"


def test_a_name_typed_before_the_suggestion_comes_stays(qtbot, tree, open_window, stuck):
    (tree / "src" / "New folder").mkdir()
    window = open_window(tree / "src")
    suggest = stuck(quick, "suggest_folder_name")
    dialog = window.new_folder()
    assert dialog.edit.text() == "New folder"

    qtbot.keyClicks(dialog.edit, "Mine")
    suggest.release()
    settle(window)

    assert dialog.edit.text() == "Mine"
    assert off_the_ui_thread(suggest)
    dialog.reject()


def test_a_drop_waits_for_its_file_systems_and_not_the_window(
    qtbot, tree, open_window, jobs, wait_final, stuck
):
    window = open_window(tree / "dst")
    same = stuck(locations, "same_filesystem")

    window._on_dropped(uris(tree / "src" / "a.txt"), None, NO_KEYS, QPoint(0, 0))
    qtbot.wait(50)

    assert jobs.jobs == []
    same.release()
    settle(window)
    job = wait_final(jobs, jobs.jobs[0])
    assert job.kind is JobKind.MOVE
    assert off_the_ui_thread(same)


def test_a_link_waits_for_its_folder_and_not_the_window(qtbot, tree, open_window, stuck):
    window = open_window(tree / "src")
    select(window, "a.txt")
    links = stuck(quick, "create_links")

    window.create_link()
    qtbot.wait(50)

    assert not (tree / "src" / "Link to a.txt").exists()
    links.release()
    settle(window)
    assert os.readlink(tree / "src" / "Link to a.txt") == "a.txt"
    assert off_the_ui_thread(links)


# Copy path, the terminal, Move to


def test_copy_path_puts_the_paths_on_the_clipboard_as_text(qtbot, tree, open_window):
    window = open_window(tree / "src")

    # Nothing selected: this folder
    window.copy_path()
    assert QApplication.clipboard().text() == str(tree / "src")

    select(window, "a.txt")
    window.copy_path()
    assert QApplication.clipboard().text() == str(tree / "src" / "a.txt")

    select(window, "a.txt", "sub")
    window._copy_path.trigger()
    assert QApplication.clipboard().text() == f"{tree / 'src' / 'sub'}\n{tree / 'src' / 'a.txt'}"
    assert window.statusBar().currentMessage() == "2 paths copied"
    # Text, not files: pasting it here does nothing
    assert clipboard.get() == ([], False)


@pytest.fixture
def terminal(monkeypatch):
    """A terminal that is "installed", and a list of the folders it was started in."""
    from filehouse.integration import desktop

    started = []
    monkeypatch.setattr(desktop, "default_terminal", lambda: "/usr/bin/a-terminal")
    monkeypatch.setattr(desktop, "open_terminal", lambda path, on_error: started.append(path))
    return started


def test_open_terminal_here_starts_it_in_this_folder(qtbot, tree, open_window, terminal):
    window = open_window(tree / "src")
    assert window._terminal.isVisible()

    window._terminal.trigger()

    assert terminal == [str(tree / "src")]
    texts = [action.text() for action in window._menu_for(None).actions()]
    assert "Open terminal here" in texts and "Copy path" in texts


def test_the_menu_of_a_folder_opens_the_terminal_in_that_folder(qtbot, tree, open_window, terminal):
    window = open_window(tree / "src")
    by_name = {info.name: info for info in (window._model.info(row) for row in range(3))}

    folder_menu = window._menu_for(by_name["sub"])
    entry = next(a for a in folder_menu.actions() if a.text() == "Open terminal here")
    entry.trigger()
    assert terminal == [str(tree / "src" / "sub")]

    # A file has no "here"
    file_menu = window._menu_for(by_name["a.txt"])
    assert "Open terminal here" not in [a.text() for a in file_menu.actions()]
    assert "Copy path" in [a.text() for a in file_menu.actions()]


def test_without_a_terminal_there_is_no_entry_for_one(qtbot, tree, open_window, monkeypatch):
    from filehouse.integration import desktop

    monkeypatch.setattr(desktop, "default_terminal", lambda: None)
    window = open_window(tree / "src")

    assert not window._terminal.isVisible()
    visible = [a.text() for a in window._menu_for(None).actions() if a.isVisible()]
    assert "Open terminal here" not in visible


@pytest.fixture
def picked(monkeypatch):
    """Stands in for the folder dialog: set `picked.path`, read `picked.asked`."""

    class Picked:
        path = ""
        asked = []

    def pick(parent, title, start):
        Picked.asked.append((title, start))
        return Picked.path

    Picked.asked = []
    monkeypatch.setattr(main_window, "pick_folder", pick)
    return Picked


def test_move_to_moves_the_selection_to_the_picked_folder(
    qtbot, tree, open_window, jobs, wait_final, picked
):
    window = open_window(tree / "src")
    select(window, "a.txt", "sub")
    picked.path = str(tree / "dst")

    job = wait_final(jobs, window.move_selected())

    assert job.kind is JobKind.MOVE and job.state is JobState.DONE
    assert picked.asked == [("Move to", str(tree / "src"))]
    assert names(tree / "dst") == ["a.txt", "sub"]
    assert names(tree / "src") == ["b.txt"]


def test_move_to_does_nothing_when_no_folder_is_picked_or_it_is_this_one(
    qtbot, tree, open_window, jobs, picked
):
    window = open_window(tree / "src")
    assert not window._move_to.isEnabled()
    assert window.move_selected() is None and picked.asked == []

    select(window, "a.txt")
    assert window._move_to.isEnabled()
    assert window.move_selected() is None  # the dialog was closed

    picked.path = str(tree / "src")
    assert window.move_selected() is None  # where it is already

    assert jobs.jobs == []
    assert names(tree / "src") == ["a.txt", "b.txt", "sub"]


def test_move_to_keeps_what_is_there_unless_told_to_replace(
    qtbot, tree, open_window, jobs, wait_final, wait_question, picked
):
    (tree / "dst" / "a.txt").write_text("old")
    window = open_window(tree / "src")
    select(window, "a.txt")
    picked.path = str(tree / "dst")

    job = window.move_selected()
    box = wait_question(jobs)
    assert isinstance(box.question, Conflict)
    box.button_for(Answer.SKIP).click()
    wait_final(jobs, job)

    assert (tree / "dst" / "a.txt").read_text() == "old"
    assert (tree / "src" / "a.txt").read_text() == "aaa"


# Permissions


def mode_of(path):
    return os.stat(path).st_mode & 0o7777


def preset_entries(window):
    return {
        action.text().partition("\t")[0]: action
        for action in window._permissions_menu(window).actions()
    }


def test_a_preset_changes_the_selected_file_and_the_list_follows(
    qtbot, tree, open_window, jobs, wait_final
):
    (tree / "src" / "a.txt").chmod(0o640)
    window = open_window(tree / "src")
    select(window, "a.txt")
    entries = preset_entries(window)
    assert [action.text() for action in entries.values()] == [
        "Private\t600",
        "Shared\t664",
        "Make executable\t755",
        "Read-only\t444",
    ]
    assert not any(action.isChecked() for action in entries.values())

    # A job like every other: queued, in the panel and the island
    job = started_by(window, entries["Read-only"].trigger)
    assert job_model.heading(job) == "Changing the permissions of “a.txt”"
    assert job_model.ICONS[job.kind] == "object-locked"
    wait_final(jobs, job)

    assert job.state is JobState.DONE
    assert window._panel.row(job).title.text() == "Changed the permissions of 1 item"
    assert mode_of(tree / "src" / "a.txt") == 0o444
    # The list hears of it from the folder's monitor, and the menu then ticks the preset
    qtbot.waitUntil(lambda: window._view.selected_infos()[0].mode == 0o444, timeout=TIMEOUT)
    assert preset_entries(window)["Read-only"].isChecked()
    assert (tree / "src" / "b.txt").stat().st_mode & 0o777 != 0o444


def test_folders_get_their_own_modes_and_nothing_inside_is_changed(qtbot, tree, open_window):
    (tree / "src" / "sub" / "inside.txt").write_text("")
    (tree / "src" / "sub" / "inside.txt").chmod(0o640)
    window = open_window(tree / "src")
    select(window, "sub")
    entries = preset_entries(window)
    assert [action.text() for action in entries.values()] == [
        "Private\t700",
        "Shared\t775",
        "Make executable",
        "Read-only\t555",
    ]
    # Says nothing about folders
    assert not entries["Make executable"].isEnabled()

    entries["Private"].trigger()

    qtbot.waitUntil(lambda: mode_of(tree / "src" / "sub") == 0o700, timeout=TIMEOUT)
    assert mode_of(tree / "src" / "sub" / "inside.txt") == 0o640


def test_files_and_folders_together_each_get_theirs(qtbot, tree, open_window, jobs, wait_final):
    window = open_window(tree / "src")
    select(window, "a.txt", "sub")
    entries = preset_entries(window)
    # Two modes: the name says enough
    assert entries["Shared"].text() == "Shared"

    entries["Shared"].trigger()
    qtbot.waitUntil(lambda: mode_of(tree / "src" / "a.txt") == 0o664, timeout=TIMEOUT)
    qtbot.waitUntil(lambda: mode_of(tree / "src" / "sub") == 0o775, timeout=TIMEOUT)

    job = wait_final(jobs, started_by(window, preset_entries(window)["Make executable"].trigger))
    assert mode_of(tree / "src" / "a.txt") == 0o755
    assert job.summary == "Changed the permissions of 1 file, folders left as they were"
    assert mode_of(tree / "src" / "sub") == 0o775


def test_a_link_is_left_alone_and_the_window_says_why(qtbot, tree, open_window, jobs, wait_final):
    (tree / "src" / "a.txt").chmod(0o640)
    (tree / "src" / "link").symlink_to("a.txt")
    window = open_window(tree / "src")
    select(window, "link")

    job = wait_final(jobs, started_by(window, preset_entries(window)["Private"].trigger))

    # Not asked about: there is nothing to try again
    assert jobs.prompter.current is None
    row = window._panel.row(job)
    assert row.title.text() == "Changed the permissions of 0 of 1 item, 1 failed"
    assert row.text.text() == "link: a link has no permissions of its own"
    assert not row.retry.isVisible()
    assert mode_of(tree / "src" / "a.txt") == 0o640


def test_several_failures_are_all_listed_by_name(qtbot, tree, open_window, jobs, wait_final):
    for name in ("one", "two"):
        (tree / "src" / name).symlink_to("a.txt")
    window = open_window(tree / "src")
    select(window, "one", "two", "b.txt")

    job = wait_final(jobs, started_by(window, preset_entries(window)["Private"].trigger))
    row = window._panel.row(job)
    assert row.title.text() == "Changed the permissions of 1 of 3 items, 2 failed"

    row.details.click()
    details = window.findChild(ErrorDetails)
    assert details.list.toPlainText() == (
        f"{tree / 'src' / 'one'}: a link has no permissions of its own\n"
        f"{tree / 'src' / 'two'}: a link has no permissions of its own"
    )
    # The one that could be changed was
    assert mode_of(tree / "src" / "b.txt") == 0o600
    details.close()


def test_permissions_need_a_selection_and_are_in_the_file_menu(qtbot, tree, open_window):
    window = open_window(tree / "src")
    assert not window._permissions.isEnabled()
    assert window.set_permissions(permissions.PRESETS[0]) is None

    select(window, "a.txt")
    assert window._permissions.isEnabled()
    menu = window._menu_for(window._view.selected_infos()[0])
    titles = [action.menu().title() for action in menu.actions() if action.menu()]
    assert "Permissions" in titles
    # Not in the folder's own menu
    folder_titles = [a.menu().title() for a in window._menu_for(None).actions() if a.menu()]
    assert "Permissions" not in folder_titles


# Drag and drop


def test_dragging_out_gives_a_uri_list(qtbot, tree, open_window):
    window = open_window(tree / "src")
    select(window, "a.txt", "sub")

    data = window._proxy.mimeData(window._view.selection_model().selectedIndexes())

    assert data.formats() == ["text/uri-list"]
    assert sorted(data.urls()) == sorted(
        QUrl.fromLocalFile(str(tree / "src" / name)) for name in ("a.txt", "sub")
    )


def drop(window, paths, onto=None, keys=NO_KEYS):
    info = None
    if onto is not None:
        info = window._model.info(window._model.row_for_name(onto))
    return started_by(window, lambda: window._on_dropped(uris(*paths), info, keys, QPoint(0, 0)))


def test_a_drop_on_the_same_file_system_moves(qtbot, tree, open_window, jobs, wait_final):
    window = open_window(tree / "dst")

    job = wait_final(jobs, drop(window, [tree / "src" / "a.txt"]))

    assert job.kind is JobKind.MOVE
    assert names(tree / "dst") == ["a.txt"]
    assert names(tree / "src") == ["b.txt", "sub"]


def test_a_drop_from_another_file_system_copies(
    qtbot, tree, open_window, jobs, wait_final, monkeypatch
):
    monkeypatch.setattr(locations, "same_filesystem", lambda a, b: False)
    window = open_window(tree / "dst")

    job = wait_final(jobs, drop(window, [tree / "src" / "a.txt"]))

    assert job.kind is JobKind.COPY
    assert names(tree / "src") == ["a.txt", "b.txt", "sub"]
    assert names(tree / "dst") == ["a.txt"]


def test_same_filesystem_is_told_from_the_files(tmp_path):
    assert locations.same_filesystem(gfile(tmp_path), gfile(tmp_path.parent))
    assert not locations.same_filesystem(gfile(tmp_path), gfile("/proc"))
    assert not locations.same_filesystem(gfile(tmp_path), gfile(tmp_path / "missing"))


def test_ctrl_copies_and_shift_moves(qtbot, tree, open_window, jobs, wait_final, monkeypatch):
    window = open_window(tree / "dst")
    keys = Qt.KeyboardModifier

    copied = wait_final(jobs, drop(window, [tree / "src" / "a.txt"], keys=keys.ControlModifier))
    assert copied.kind is JobKind.COPY
    assert (tree / "src" / "a.txt").exists()

    monkeypatch.setattr(locations, "same_filesystem", lambda a, b: False)
    moved = wait_final(jobs, drop(window, [tree / "src" / "b.txt"], keys=keys.ShiftModifier))
    assert moved.kind is JobKind.MOVE
    assert not (tree / "src" / "b.txt").exists()


def test_alt_asks_with_a_menu(qtbot, tree, open_window, jobs, wait_final, monkeypatch):
    window = open_window(tree / "dst")
    choices = iter([JobKind.COPY, None])
    monkeypatch.setattr(window._file_actions, "ask_drop_action", lambda position: next(choices))
    alt = Qt.KeyboardModifier.AltModifier

    job = wait_final(jobs, drop(window, [tree / "src" / "a.txt"], keys=alt))
    assert job.kind is JobKind.COPY

    assert drop(window, [tree / "src" / "b.txt"], keys=alt) is None
    assert names(tree / "src") == ["a.txt", "b.txt", "sub"]


def test_a_drop_on_a_folder_goes_into_it(qtbot, tree, open_window, jobs, wait_final):
    window = open_window(tree / "src")

    wait_final(jobs, drop(window, [tree / "src" / "a.txt"], onto="sub"))

    assert names(tree / "src" / "sub") == ["a.txt"]
    assert names(tree / "src") == ["b.txt", "sub"]


def test_drops_that_would_change_nothing_are_ignored(qtbot, tree, open_window, jobs):
    window = open_window(tree / "src")

    # Back where it came from, and a folder on itself
    assert drop(window, [tree / "src" / "a.txt"]) is None
    assert drop(window, [tree / "src" / "sub"], onto="sub") is None

    assert jobs.jobs == []
    assert names(tree / "src") == ["a.txt", "b.txt", "sub"]


# The panel


def test_the_panel_shows_jobs_and_hides_without_them(
    qtbot, tree, open_window, jobs, wait_final, wait_question
):
    (tree / "dst" / "a.txt").write_text("old")
    window = open_window(tree / "dst")
    assert not window._panel.isVisible()
    clipboard.put(uris(tree / "src" / "a.txt"), cut=False)

    job = window.paste()
    box = wait_question(jobs)
    row = window._panel.row(job)

    assert window._panel.isVisible()
    assert window._panel._header.text() == "1 operation running"
    assert row.title.text() == "Copying “a.txt” to dst"
    assert row.cancel.isVisible() and not row.dismiss.isVisible()

    box.button_for(Answer.REPLACE).click()
    wait_final(jobs, job)

    assert row.title.text() == "Copied 1 file"
    assert not row.cancel.isVisible() and not row.bar.isVisible()
    assert row.dismiss.isVisible() and not row.retry.isVisible()
    assert window._panel._header.text() == "Operations finished"

    row.dismiss.click()
    assert jobs.jobs == []
    assert not window._panel.isVisible()


def test_cancel_in_the_panel_stops_a_job_and_closes_its_question(
    qtbot, tree, open_window, jobs, wait_final, wait_question
):
    (tree / "dst" / "a.txt").write_text("old")
    window = open_window(tree / "dst")
    clipboard.put(uris(tree / "src" / "a.txt"), cut=False)
    job = window.paste()
    box = wait_question(jobs)

    window._panel.row(job).cancel.click()
    wait_final(jobs, job)

    assert job.state is JobState.CANCELLED
    assert window._panel.row(job).title.text() == "Cancelled after 0 of 1 file"
    assert jobs.prompter.current is None
    assert not isValid(box) or not box.isVisible()
    assert (tree / "dst" / "a.txt").read_text() == "old"


@needs_permissions
def test_a_failed_job_stays_with_retry_and_details(
    qtbot, tree, open_window, jobs, wait_final, wait_question
):
    locked = tree / "src" / "a.txt"
    locked.chmod(0)
    window = open_window(tree / "dst")
    clipboard.put(uris(locked, tree / "src" / "b.txt"), cut=False)

    job = window.paste()
    box = wait_question(jobs)
    assert isinstance(box.question, Failure)
    assert box.text() == "Could not copy “a.txt”: permission denied."
    box.button_for(Answer.SKIP).click()
    wait_final(jobs, job)

    row = window._panel.row(job)
    assert row.title.text() == "Copied 1 of 2 files, 1 skipped"
    assert row.text.text() == "a.txt: permission denied"
    assert row.retry.isVisible() and row.details.isVisible() and row.dismiss.isVisible()
    assert window._panel._header.text() == "1 operation failed"

    row.details.click()
    details = window.findChild(ErrorDetails)
    assert details.list.toPlainText() == f"{locked}: permission denied"
    details.close()

    locked.chmod(0o600)
    row.retry.click()
    again = jobs.jobs[0]
    wait_final(jobs, again)

    assert again is not job and again.state is JobState.DONE
    assert window._panel.row(job) is None
    assert (tree / "dst" / "a.txt").read_text() == "aaa"


def test_every_window_shows_the_same_jobs(
    qtbot, tree, open_window, jobs, wait_final, wait_question
):
    (tree / "dst" / "a.txt").write_text("old")
    first = open_window(tree / "dst")
    second = open_window(tree / "src")
    clipboard.put(uris(tree / "src" / "a.txt"), cut=False)

    job = first.paste()
    box = wait_question(jobs)

    assert second._panel.row(job) is not None
    box.button_for(Answer.SKIP).click()
    wait_final(jobs, job)
    second._panel.row(job).dismiss.click()
    assert first._panel.row(job) is None


def test_a_job_that_needs_nothing_runs_without_opening_the_panel(
    qtbot, tree, open_window, jobs, wait_final
):
    window = open_window(tree / "dst")

    job = wait_final(jobs, drop(window, [tree / "src" / "a.txt"]))

    assert window._panel.row(job) is not None
    assert not window._panel.isVisible()
    # The status bar says how it ended
    assert window._job_status.isVisible()
    assert window._job_status.title.text() == "Moved 1 file"


def test_the_status_bar_opens_the_panel_and_its_header_closes_it(
    qtbot, tree, open_window, jobs, wait_final
):
    window = open_window(tree / "dst")
    wait_final(jobs, drop(window, [tree / "src" / "a.txt"]))

    window._job_status.click()
    assert window._panel.isVisible() and window._panel.is_open

    window._panel._header.click()
    assert not window._panel.isVisible()

    window._job_status.click()
    window._job_status.click()
    assert not window._panel.isVisible()


def test_the_panel_slides_open_and_shut(qtbot, tree, open_window, jobs, wait_final, monkeypatch):
    from filehouse.integration import motion

    monkeypatch.setattr(motion, "factor", lambda: 1.0)
    window = open_window(tree / "dst")
    wait_final(jobs, drop(window, [tree / "src" / "a.txt"]))
    panel = window._panel

    window._job_status.click()
    assert panel.is_open and panel.isVisible() and panel.maximumHeight() == 0
    qtbot.waitUntil(lambda: panel.height() == panel.sizeHint().height(), timeout=TIMEOUT)

    panel._header.click()
    # Closed, and on its way out
    assert not panel.is_open and panel.isVisible()
    qtbot.waitUntil(lambda: not panel.isVisible(), timeout=TIMEOUT)


def test_a_question_opens_the_panel_and_a_closed_panel_stays_closed(
    qtbot, tree, open_window, jobs, wait_final, wait_question
):
    (tree / "dst" / "a.txt").write_text("old")
    window = open_window(tree / "dst")
    clipboard.put(uris(tree / "src" / "a.txt"), cut=False)

    job = window.paste()
    box = wait_question(jobs)
    assert window._panel.isVisible()
    assert window._job_status.title.text() == "Copying “a.txt”"

    # Closed by the user: more news about the same wait doesn't open it again
    window._panel.set_open(False)
    jobs.job_changed.emit(job)
    assert not window._panel.isVisible()

    box.button_for(Answer.SKIP).click()
    wait_final(jobs, job)
    assert not window._panel.isVisible()


@needs_permissions
def test_a_failure_opens_the_panel_in_every_window(
    qtbot, tree, open_window, jobs, wait_final, wait_question
):
    (tree / "src" / "a.txt").chmod(0)
    window = open_window(tree / "dst")
    other = open_window(tree / "src")
    clipboard.put(uris(tree / "src" / "a.txt"), cut=False)
    job = window.paste()
    wait_question(jobs).button_for(Answer.SKIP).click()
    wait_final(jobs, job)
    assert job.state is JobState.FAILED
    assert window._panel.isVisible() and other._panel.isVisible()
    assert window._job_status.title.text() == "1 operation failed"

    # Closed by the user: more news about the same failure leaves it closed
    for each in (window, other):
        each._panel.set_open(False)
    jobs.job_changed.emit(job)
    assert not window._panel.isVisible()

    # A retry that fails as well is a new failure
    again = jobs.retry(job)
    wait_question(jobs).button_for(Answer.SKIP).click()
    wait_final(jobs, again)
    assert window._panel.isVisible() and other._panel.isVisible()
    assert window._job_status.title.text() == "1 operation failed"


def test_the_panel_starts_closed_again_after_the_jobs_are_gone(
    qtbot, tree, open_window, jobs, wait_final
):
    window = open_window(tree / "dst")
    job = wait_final(jobs, drop(window, [tree / "src" / "a.txt"]))
    window._job_status.click()
    assert window._panel.isVisible()

    jobs.dismiss(job)
    assert not window._panel.isVisible() and not window._job_status.isVisible()

    wait_final(jobs, drop(window, [tree / "src" / "b.txt"]))
    assert not window._panel.isVisible()


def test_item_counts_are_on_the_left_and_operations_on_the_right(
    qtbot, tree, open_window, jobs, wait_final
):
    window = open_window(tree / "src")
    wait_final(jobs, drop(window, [tree / "dst"], keys=Qt.KeyboardModifier.ControlModifier))
    qtbot.waitUntil(lambda: window._job_status.isVisible(), timeout=TIMEOUT)

    assert window._count_label.isVisible()
    assert window._count_label.geometry().right() < window._job_status.geometry().left()


# Closing


@pytest.fixture
def waiting_job(qtbot, tree, open_window, jobs, wait_question, monkeypatch):
    """A window with one job that waits for an answer, and a record of what closing did."""
    (tree / "dst" / "a.txt").write_text("old")
    window = open_window(tree / "dst")
    clipboard.put(uris(tree / "src" / "a.txt"), cut=False)
    job = window.paste()
    box = wait_question(jobs)
    quits = []
    monkeypatch.setattr(main_window, "quit_app", lambda: quits.append(True))
    return window, job, box, quits


def answer_close_with(monkeypatch, choice):
    asked = []

    def ask(parent, count):
        asked.append(count)
        return choice

    monkeypatch.setattr(main_window, "ask_about_running_jobs", ask)
    return asked


def test_closing_with_a_running_job_can_be_called_off(qtbot, waiting_job, monkeypatch):
    window, job, _box, quits = waiting_job
    asked = answer_close_with(monkeypatch, main_window.STAY_OPEN)

    assert not window.close()

    assert asked == [1]
    assert window.isVisible()
    assert job.state is JobState.WAITING
    assert not quits


def test_keep_running_hides_the_window_until_the_jobs_end(
    qtbot, waiting_job, monkeypatch, tree, jobs, wait_final
):
    window, job, box, quits = waiting_job
    asked = answer_close_with(monkeypatch, main_window.KEEP_RUNNING)

    window.close()

    assert asked == [1]
    assert not window.isVisible()
    assert job.state is JobState.WAITING
    assert not quits

    # The question is still on screen, and answering it lets the job finish
    box.button_for(Answer.REPLACE).click()
    wait_final(jobs, job)
    qtbot.waitUntil(lambda: quits == [True], timeout=TIMEOUT)

    assert job.state is JobState.DONE
    assert (tree / "dst" / "a.txt").read_text() == "aaa"


def test_cancelling_on_close_waits_for_the_jobs_to_stop(
    qtbot, waiting_job, monkeypatch, tree, jobs, wait_final
):
    window, job, _box, quits = waiting_job
    answer_close_with(monkeypatch, main_window.CANCEL_ALL)

    window.close()
    assert not quits
    wait_final(jobs, job)
    qtbot.waitUntil(lambda: quits == [True], timeout=TIMEOUT)

    assert job.state is JobState.CANCELLED
    assert (tree / "dst" / "a.txt").read_text() == "old"


def test_closing_one_of_two_windows_does_not_ask(
    qtbot, waiting_job, monkeypatch, tree, open_window
):
    window, job, _box, quits = waiting_job
    other = open_window(tree / "src")
    other.show()
    asked = answer_close_with(monkeypatch, main_window.STAY_OPEN)

    assert window.close()

    assert asked == []
    assert job.state is JobState.WAITING
    assert not quits


def test_closing_without_jobs_just_closes(qtbot, tree, open_window, monkeypatch):
    window = open_window(tree / "src")
    asked = answer_close_with(monkeypatch, main_window.STAY_OPEN)

    assert window.close()
    assert asked == []


def test_a_question_whose_window_closes_is_asked_again(
    qtbot, waiting_job, tree, open_window, jobs, wait_final
):
    window, job, box, _quits = waiting_job
    other = open_window(tree / "src")
    assert box.parent() is window

    window.close()
    qtbot.waitUntil(
        lambda: jobs.prompter.current is not None and jobs.prompter.current is not box,
        timeout=TIMEOUT,
    )
    again = jobs.prompter.current

    assert again.question is box.question
    assert again.parent() is other
    again.button_for(Answer.REPLACE).click()
    wait_final(jobs, job)
    assert (tree / "dst" / "a.txt").read_text() == "aaa"


# What is remembered


def test_hidden_files_and_view_mode_are_remembered(qtbot, tree, open_window):
    # One view for every folder: with a view per folder, see test_folder_views.py
    settings.set_per_folder(False)
    first = open_window(tree / "src")
    assert first._view.mode == settings.DETAILS and not first._hidden.isChecked()

    first._hidden.trigger()
    first._icons_mode.trigger()
    second = open_window(tree / "dst")

    assert second._hidden.isChecked() and second._proxy.show_hidden
    assert second._view.mode == settings.ICONS
    assert second._icons_mode.isChecked() and not second._details_mode.isChecked()


def test_window_size_is_remembered_when_it_closes(qtbot, tree, open_window):
    first = open_window(tree / "src")
    first.resize(701, 503)
    first.close()

    second = open_window(tree / "src")

    assert (second.width(), second.height()) == (701, 503)


# Icons and details


def test_changing_the_view_mode_keeps_the_selection(qtbot, tree, open_window):
    window = open_window(tree / "src")
    select(window, "a.txt", "sub")
    view = window._view

    window._icons_mode.trigger()

    assert view._stack.currentWidget() is view._grid
    assert [info.name for info in view.selected_infos()] == ["sub", "a.txt"]
    assert window._count_label.text() == "3 items · 2 selected"
    assert window._model._thumbnails_on

    # Selecting in the icons counts as well: they only ever select the name
    view._grid.selectionModel().clearSelection()
    view._grid.setCurrentIndex(window._proxy.index(1, 0))
    assert view.selected_count() == 1
    assert window._rename.isEnabled()

    qtbot.keyClick(window, Qt.Key.Key_3, Qt.KeyboardModifier.ControlModifier)
    assert view._stack.currentWidget() is view._tree
    assert not window._model._thumbnails_on


def test_an_empty_folder_shows_its_message_in_both_modes(qtbot, tree, open_window):
    window = open_window(tree / "dst")
    view = window._view
    assert view._stack.currentWidget() is view._message

    window._icons_mode.trigger()
    assert view._stack.currentWidget() is view._message

    (tree / "dst" / "new.txt").write_text("x")
    qtbot.waitUntil(lambda: view._stack.currentWidget() is view._grid, timeout=TIMEOUT)


def test_operations_work_from_the_icons(qtbot, tree, open_window, jobs, wait_final):
    window = open_window(tree / "src")
    window._icons_mode.trigger()
    select(window, "a.txt")
    window._copy.trigger()
    go(qtbot, window, tree / "dst")

    wait_final(jobs, window.paste())

    assert names(tree / "dst") == ["a.txt"]


# The trash, with a folder standing in for it: the real one is never opened here


@pytest.fixture
def bin_(tree, monkeypatch):
    """A folder the app takes for the trash, holding two trashed items."""
    trash = tree / "bin"
    trash.mkdir()
    (trash / "report.pdf").write_text("r")
    (trash / "album").mkdir()
    (trash / "album" / "a.jpg").write_text("a")
    root = gfile(trash)
    monkeypatch.setattr(locations, "trash", lambda: root)
    monkeypatch.setattr(locations, "is_trash", lambda location: location.equal(root))
    monkeypatch.setattr(
        locations, "in_trash", lambda location: location.equal(root) or location.has_prefix(root)
    )
    # Where each item was, as the real trash tells for what is in it
    real = directory_model.info_from_gio

    def with_origin(info):
        made = real(info)
        if made.name in ("report.pdf", "album"):
            made = dataclasses.replace(made, original_path=str(tree / "src" / made.name))
        return made

    # Both the first listing and the changes that follow go through this
    monkeypatch.setattr(listing, "info_from_gio", with_origin)
    monkeypatch.setattr(directory_model, "info_from_gio", with_origin)
    return trash


def test_the_trash_offers_restore_and_delete_and_nothing_else(qtbot, tree, bin_, open_window):
    clipboard.put(uris(tree / "src" / "a.txt"), cut=False)
    window = open_window(bin_)
    assert window.windowTitle() == "Trash — Files"

    off = (window._cut, window._paste, window._rename, window._new_folder)
    assert not any(action.isEnabled() for action in off)
    assert window._empty_trash.isEnabled()
    assert not window._restore.isEnabled() and not window._delete.isEnabled()

    select(window, "report.pdf")
    assert window._restore.isEnabled() and window._delete.isEnabled()
    assert not any(action.isEnabled() for action in off)
    assert window.paste() is None and window.new_folder() is None and window.rename() is None


def test_restore_puts_the_selection_back(qtbot, tree, bin_, open_window, jobs, wait_final):
    window = open_window(bin_)
    select(window, "report.pdf", "album")

    job = wait_final(jobs, window.restore_selected())

    assert job.kind is JobKind.RESTORE and job.state is JobState.DONE
    assert window._panel.row(job).title.text() == "Restored 2 items"
    assert names(bin_) == []
    assert (tree / "src" / "report.pdf").read_text() == "r"
    assert names(tree / "src" / "album") == ["a.jpg"]
    qtbot.waitUntil(lambda: listed(window) == [], timeout=TIMEOUT)
    assert window._view._message.text() == "The trash is empty"
    assert not window._empty_trash.isEnabled()


def test_restore_asks_when_something_is_in_the_way(
    qtbot, tree, bin_, open_window, jobs, wait_final, wait_question
):
    (tree / "src" / "report.pdf").write_text("newer")
    window = open_window(bin_)
    select(window, "report.pdf")

    job = window.restore_selected()
    box = wait_question(jobs)

    with pytest.raises(StopIteration):
        box.button_for(Answer.REPLACE)
    box.button_for(Answer.KEEP_BOTH).click()
    wait_final(jobs, job)
    assert (tree / "src" / "report.pdf").read_text() == "newer"
    assert (tree / "src" / "report (2).pdf").read_text() == "r"


def test_delete_key_in_the_trash_deletes_for_good_after_asking(
    qtbot, tree, bin_, open_window, jobs, wait_final, wait_question
):
    window = open_window(bin_)
    select(window, "report.pdf")

    qtbot.keyClick(window._view._tree, Qt.Key.Key_Delete)
    box = wait_question(jobs)

    assert isinstance(box.question, ConfirmDelete)
    assert names(bin_) == ["album", "report.pdf"]
    box.button_for(Answer.DELETE).click()
    job = wait_final(jobs, jobs.jobs[0])
    assert job.kind is JobKind.DELETE
    assert names(bin_) == ["album"]


def test_empty_trash_asks_with_the_count_and_deletes_everything(
    qtbot, tree, bin_, open_window, jobs, wait_final, wait_question
):
    (bin_ / ".hidden").write_text("h")
    window = open_window(bin_)

    job = window.empty_trash()
    box = wait_question(jobs)

    # Hidden items count too: the trash is emptied, not just what is on show
    assert box.text() == "Permanently delete 3 items?"
    assert names(bin_) == [".hidden", "album", "report.pdf"]
    box.button_for(Answer.DELETE).click()
    wait_final(jobs, job)

    assert job.state is JobState.DONE
    assert names(bin_) == []
    assert names(tree / "src") == ["a.txt", "b.txt", "sub"]


def test_empty_trash_can_be_called_off(
    qtbot, tree, bin_, open_window, jobs, wait_final, wait_question
):
    window = open_window(bin_)

    job = window.empty_trash()
    wait_question(jobs).button_for(Answer.CANCEL).click()
    wait_final(jobs, job)

    assert names(bin_) == ["album", "report.pdf"]


def test_empty_trash_is_only_for_the_trash(qtbot, tree, open_window, jobs):
    window = open_window(tree / "src")

    assert not window._empty_trash.isEnabled()
    assert window.empty_trash() is None
    assert window.restore_selected() is None
    assert jobs.jobs == []


def test_inside_a_trashed_folder_nothing_can_be_restored_or_deleted(
    qtbot, tree, bin_, open_window, jobs
):
    window = open_window(bin_ / "album")
    select(window, "a.jpg")

    assert not window._restore.isEnabled() and not window._delete.isEnabled()
    assert window.delete_selected() is None and window.trash_selected() is None
    assert not window._empty_trash.isEnabled()
    assert jobs.jobs == []


def test_a_drop_on_the_trash_trashes(qtbot, home_dir, tree, bin_, open_window, jobs, wait_final):
    (home_dir / "old.txt").write_text("x")
    window = open_window(bin_)

    job = wait_final(jobs, drop(window, [home_dir / "old.txt"]))

    assert job.kind is JobKind.TRASH and job.state is JobState.DONE
    assert names(home_dir) == []
    # Not onto a trashed folder, and what is in the trash already stays put
    assert drop(window, [tree / "src" / "a.txt"], onto="album") is None
    assert drop(window, [bin_ / "report.pdf"]) is None


def test_a_file_in_the_trash_is_not_opened(qtbot, tree, bin_, open_window):
    window = open_window(bin_)

    window._on_item_activated(window._model.info(window._model.row_for_name("report.pdf")))

    assert "Restore" in window.statusBar().currentMessage()


# Properties


def test_properties_of_a_file(qtbot, tree, open_window):
    window = open_window(tree / "src")
    select(window, "a.txt")

    dialog = window.properties()

    assert dialog.windowTitle() == "Properties of a.txt"
    rows = {label: value.text() for label, value in dialog.rows.items()}
    assert rows["Name"] == "a.txt"
    assert rows["Type"] == "Plain text document"
    assert rows["Location"] == str(tree / "src")
    assert rows["Size"] == "3 bytes"
    assert rows["Modified"]
    assert rows["Others"] in ("Read", "Nothing", "Read, write")
    assert any(label.startswith("Owner") for label in rows)
    dialog.close()


def test_properties_of_a_folder_add_up_what_is_in_it(qtbot, tree, open_window):
    (tree / "src" / "sub" / "big.bin").write_bytes(b"x" * 3000)
    (tree / "src" / "sub" / "inner").mkdir()
    window = open_window(tree / "src")
    select(window, "sub")

    dialog = window.properties()
    assert dialog.rows["Size"].text() == "Calculating…"

    qtbot.waitUntil(lambda: "file" in dialog.rows["Size"].text(), timeout=TIMEOUT)
    assert dialog.rows["Size"].text().endswith("(3,000 bytes), 1 file, 1 folder") or dialog.rows[
        "Size"
    ].text().endswith("bytes), 1 file, 1 folder")
    dialog.close()


def test_properties_of_several_items_and_of_the_open_folder(qtbot, tree, open_window):
    window = open_window(tree / "src")
    select(window, "a.txt", "b.txt", "sub")

    several = window.properties()

    assert several.windowTitle() == "Properties of 3 items"
    assert several.rows["Location"].text() == str(tree / "src")
    qtbot.waitUntil(lambda: "files" in several.rows["Size"].text(), timeout=TIMEOUT)
    assert several.rows["Size"].text() == "6 bytes, 2 files, 1 folder"
    several.close()

    select(window)
    folder = window.properties()
    assert folder.windowTitle() == "Properties of src"
    qtbot.waitUntil(lambda: "files" in folder.rows["Size"].text(), timeout=TIMEOUT)
    assert folder.rows["Size"].text() == "6 bytes, 2 files, 1 folder"
    folder.close()


def test_properties_of_something_that_is_gone_say_so(qtbot, tree, open_window):
    window = open_window(tree / "src")
    select(window, "a.txt")
    (tree / "src" / "a.txt").unlink()

    dialog = window.properties()

    assert "no such file" in dialog.rows["Problem"].text()
    dialog.close()


# Volumes


def test_a_window_leaves_a_drive_that_goes_away(qtbot, tree, open_window):
    window = open_window(tree / "src" / "sub")

    window._on_volume_removed(gfile(tree / "dst"))
    assert window._location.get_path() == str(tree / "src" / "sub")

    window._on_volume_removed(gfile(tree / "src"))
    wait_loaded(qtbot, window)
    assert locations.same(window._location, locations.home())
    assert "gone" in window.statusBar().currentMessage()
