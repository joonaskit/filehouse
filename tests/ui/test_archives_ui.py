"""Archives from the window: the menu, the dialogs, and the jobs they start."""

import zipfile

import pytest
from PySide6.QtWidgets import QDialogButtonBox
from shiboken6 import isValid
from ui_helpers import TIMEOUT, gfile

from filehouse.core import archives
from filehouse.core.operations import Answer, JobKind, JobState
from filehouse.qt_models import job_model
from filehouse.ui import main_window
from filehouse.ui.dialogs.compress import CompressDialog
from filehouse.ui.dialogs.questions import PasswordBox
from filehouse.ui.main_window import MainWindow

pytestmark = pytest.mark.skipif(not archives.available(), reason="libarchive is not installed")


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "work").mkdir()
    (tmp_path / "work" / "notes.txt").write_text("notes")
    (tmp_path / "work" / "sub").mkdir()
    (tmp_path / "work" / "sub" / "b.txt").write_text("bb")
    with zipfile.ZipFile(tmp_path / "work" / "pack.zip", "w") as archive:
        archive.writestr("top/a.txt", "aaa")
        archive.writestr("top/more/b.txt", "bb")
    return tmp_path / "work"


@pytest.fixture
def window(qtbot, jobs, tree):
    window = MainWindow(gfile(tree), jobs=jobs)
    window.show()
    qtbot.waitUntil(lambda: not window._model.loading, timeout=TIMEOUT)
    yield window
    jobs.cancel_all()
    qtbot.waitUntil(lambda: jobs.active_count() == 0, timeout=TIMEOUT)
    if isValid(window):
        window.close()


def select(window, *names):
    model = window._view.selection_model()
    model.clearSelection()
    for name in names:
        source = window._model.index(window._model.row_for_name(name), 0)
        model.select(
            window._proxy.mapFromSource(source),
            model.SelectionFlag.Select | model.SelectionFlag.Rows,
        )


def info(window, name):
    return window._model.info(window._model.row_for_name(name))


def test_an_archive_can_be_extracted_from_its_menu_and_other_files_cannot(window):
    select(window, "pack.zip")
    archive = window._menu_for(info(window, "pack.zip")).actions()
    select(window, "notes.txt")
    other = window._menu_for(info(window, "notes.txt")).actions()

    assert window._extract_here in archive and window._extract_to in archive
    assert window._extract_here not in other and window._extract_to not in other
    assert window._compress in archive and window._compress in other
    assert not window._extract_here.isEnabled()


def test_nothing_is_offered_without_libarchive(window, monkeypatch):
    monkeypatch.setattr(archives, "libarchive", None)
    select(window, "pack.zip")

    actions = window._menu_for(info(window, "pack.zip")).actions()

    assert window._extract_here not in actions and window._compress not in actions
    assert window.compress() is None and window.extract_here() == []


def test_extract_here_makes_the_folder_beside_the_archive(qtbot, window, tree, jobs, wait_final):
    select(window, "pack.zip")

    (job,) = window.extract_here()
    assert job.kind is JobKind.EXTRACT
    assert job_model.heading(job) == "Extracting “pack.zip” to work"
    wait_final(jobs, job)

    assert job.state is JobState.DONE
    assert job_model.title(job) == "Extracted 2 files"
    assert (tree / "top" / "more" / "b.txt").read_text() == "bb"
    assert jobs.origin(job) is window


def test_extract_to_asks_for_the_folder(qtbot, window, tree, jobs, wait_final, monkeypatch):
    (tree / "sub" / "into").mkdir()
    asked = []

    def pick(parent, title, start):
        asked.append((title, start))
        return str(tree / "sub" / "into")

    monkeypatch.setattr(main_window, "pick_folder", pick)
    select(window, "pack.zip")

    (job,) = window.extract_to()
    wait_final(jobs, job)

    assert asked == [("Extract to", str(tree))]
    assert (tree / "sub" / "into" / "top" / "a.txt").read_text() == "aaa"
    assert not (tree / "top").exists()


def test_extract_to_does_nothing_when_no_folder_is_picked(window, jobs, monkeypatch):
    monkeypatch.setattr(main_window, "pick_folder", lambda *_: "")
    select(window, "pack.zip")

    assert window.extract_to() == []
    assert jobs.jobs == []


def test_a_taken_name_is_asked_about_and_kept_beside(
    qtbot, window, tree, jobs, wait_final, wait_question
):
    (tree / "top").mkdir()
    (tree / "top" / "mine.txt").write_text("keep me")
    select(window, "pack.zip")

    (job,) = window.extract_here()
    box = wait_question(jobs)
    assert "“top” already exists in work" in box.text()
    assert "not replaced" in box.informativeText()
    box.button_for(Answer.KEEP_BOTH).click()
    wait_final(jobs, job)

    assert job.state is JobState.DONE
    assert [path.name for path in (tree / "top").iterdir()] == ["mine.txt"]
    assert (tree / "top (2)" / "a.txt").read_text() == "aaa"


def test_a_protected_archive_asks_for_its_password(
    qtbot, window, tree, jobs, wait_final, wait_question
):
    import libarchive

    with libarchive.file_writer(
        str(tree / "secret.zip"), "zip", options="zip:encryption=aes256", passphrase="open sesame"
    ) as archive:
        archive.add_file_from_memory("hidden.txt", 5, b"hello")
    qtbot.waitUntil(lambda: window._model.row_for_name("secret.zip") >= 0, timeout=TIMEOUT)
    select(window, "secret.zip")

    (job,) = window.extract_here()
    box = wait_question(jobs)
    assert isinstance(box, PasswordBox) and not box.question.wrong
    box.edit.setText("guess")
    box.accept()
    qtbot.waitUntil(
        lambda: jobs.prompter.current is not None and jobs.prompter.current is not box,
        timeout=TIMEOUT,
    )
    again = jobs.prompter.current
    assert again.question.wrong
    again.edit.setText("open sesame")
    again.accept()
    wait_final(jobs, job)

    assert job.state is JobState.DONE
    assert (tree / "hidden.txt").read_text() == "hello"


def test_closing_the_password_question_cancels(
    qtbot, window, tree, jobs, wait_final, wait_question
):
    import libarchive

    with libarchive.file_writer(
        str(tree / "secret.zip"), "zip", options="zip:encryption=aes256", passphrase="x"
    ) as archive:
        archive.add_file_from_memory("hidden.txt", 5, b"hello")
    qtbot.waitUntil(lambda: window._model.row_for_name("secret.zip") >= 0, timeout=TIMEOUT)
    select(window, "secret.zip")

    (job,) = window.extract_here()
    wait_question(jobs).reject()
    wait_final(jobs, job)

    assert job.state is JobState.CANCELLED
    assert not (tree / "hidden.txt").exists()


def test_compress_asks_for_a_name_and_a_kind_and_makes_the_archive(
    qtbot, window, tree, jobs, wait_final
):
    select(window, "notes.txt", "sub")

    dialog = window.compress()
    assert isinstance(dialog, CompressDialog)
    assert dialog.edit.text() == "work.zip"
    assert dialog.edit.selectedText() == "work"
    dialog.format.setCurrentIndex(dialog.format.findData("tar.gz"))
    assert dialog.edit.text() == "work.tar.gz"
    dialog.edit.setText("backup")
    dialog.buttons.button(QDialogButtonBox.StandardButton.Ok).click()
    (job,) = jobs.jobs
    assert job.kind is JobKind.COMPRESS
    assert job_model.heading(job) == "Compressing 2 items"
    wait_final(jobs, job)

    assert job.state is JobState.DONE
    assert job_model.title(job) == "Compressed 2 files"
    import tarfile

    with tarfile.open(tree / "backup.tar.gz") as archive:
        assert sorted(archive.getnames()) == ["notes.txt", "sub", "sub/b.txt"]
    assert (tree / "notes.txt").read_text() == "notes"


def test_one_item_gives_the_archive_its_name(window):
    select(window, "notes.txt")

    dialog = window.compress()

    assert dialog.edit.text() == "notes.zip"
    dialog.reject()


@pytest.mark.parametrize("name", ["", "   ", "a/b", ".."])
def test_a_name_that_cannot_be_used_is_said_and_nothing_starts(window, jobs, name):
    select(window, "notes.txt")
    dialog = window.compress()
    dialog.edit.setText(name)

    dialog.accept()

    assert dialog.isVisible() and dialog.error.isVisible()
    assert jobs.jobs == []
    dialog.reject()


def test_a_name_with_its_own_ending_keeps_it_when_the_kind_changes(window):
    select(window, "notes.txt")
    dialog = window.compress()
    dialog.edit.setText("mine.backup")

    dialog.format.setCurrentIndex(1)

    assert dialog.edit.text() == "mine.backup"
    dialog.reject()
