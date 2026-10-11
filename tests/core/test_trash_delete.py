import os
from pathlib import Path

import pytest
from conftest import not_supported
from helpers import make_tree, never_done, read_tree, run_job

from filehouse.core.operations import (
    Answer,
    ConfirmDelete,
    JobKind,
    JobState,
    Reply,
    TrashUnsupported,
    trash,
)

needs_permissions = pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permissions")


def trashed():
    files = Path(os.environ["XDG_DATA_HOME"]) / "Trash" / "files"
    return sorted(path.name for path in files.iterdir()) if files.is_dir() else []


def test_trash_moves_items_to_the_trash(home_dir):
    make_tree(home_dir, {"report.pdf": "r", "album": {"a.jpg": "a"}, "stays.txt": "s"})
    before = trashed()

    job = run_job(JobKind.TRASH, [home_dir / "report.pdf", home_dir / "album"])

    assert job.state is JobState.DONE
    assert job.summary == "Moved 2 items to the trash"
    assert read_tree(home_dir) == {"stays.txt": "s"}
    assert len(trashed()) == len(before) + 2


def test_trash_takes_the_link_not_its_target(home_dir):
    make_tree(home_dir, {"real": {"f": "x"}, "shortcut": ("link", "real")})

    job = run_job(JobKind.TRASH, [home_dir / "shortcut"])

    assert job.state is JobState.DONE
    assert read_tree(home_dir) == {"real": {"f": "x"}}


def test_cancelled_trash_says_how_far_it_got(home_dir):
    make_tree(home_dir, {"1": "", "2": "", "3": ""})

    job = run_job(
        JobKind.TRASH,
        [home_dir / name for name in "123"],
        hook=lambda job: job.progress.files_done == 1 and job.cancel(),
    )

    assert job.state is JobState.CANCELLED
    assert job.summary == "Moved 1 of 3 items to the trash"
    assert read_tree(home_dir) == {"2": "", "3": ""}


def test_trash_never_falls_back_to_delete_on_its_own(tmp_path, monkeypatch):
    monkeypatch.setattr(trash, "_gio_trash", not_supported)
    make_tree(tmp_path, {"a.txt": "a", "dir": {"b": "b"}})

    job = run_job(JobKind.TRASH, [tmp_path / "a.txt", tmp_path / "dir"])

    assert job.state is JobState.FAILED
    assert never_done(job)
    assert str(job.error) == "a.txt: cannot be moved to the trash here"
    assert job.summary == "Moved 0 of 2 items to the trash, 2 skipped"
    assert read_tree(tmp_path) == {"a.txt": "a", "dir": {"b": "b"}}


def test_a_real_file_system_without_trash_asks_first(real_other_device):
    make_tree(real_other_device, {"a.txt": "a"})
    asked = []

    def prompt(question):
        asked.append(question)
        return Answer.SKIP

    job = run_job(JobKind.TRASH, [real_other_device / "a.txt"], prompt=prompt)

    assert job.state is JobState.DONE
    assert [type(question) for question in asked] == [TrashUnsupported]
    assert asked[0].location.get_path() == str(real_other_device / "a.txt")
    assert read_tree(real_other_device) == {"a.txt": "a"}


def test_without_trash_the_user_can_choose_to_delete(tmp_path, monkeypatch):
    monkeypatch.setattr(trash, "_gio_trash", not_supported)
    make_tree(tmp_path, {"a.txt": "a", "dir": {"b": "b", "c": "c"}, "keep": "k"})
    asked = []

    def prompt(question):
        asked.append(question.location.get_basename())
        return Reply(Answer.DELETE, apply_to_all=True)

    job = run_job(JobKind.TRASH, [tmp_path / "a.txt", tmp_path / "dir"], prompt=prompt)

    assert asked == ["a.txt"]
    assert job.state is JobState.DONE
    assert read_tree(tmp_path) == {"keep": "k"}


def test_delete_without_anyone_to_confirm_is_refused(tmp_path):
    make_tree(tmp_path, {"a.txt": "a"})

    job = run_job(JobKind.DELETE, [tmp_path / "a.txt"])

    assert job.state is JobState.FAILED
    assert "needs to be confirmed" in job.summary
    assert read_tree(tmp_path) == {"a.txt": "a"}


def test_delete_asks_before_touching_anything(tmp_path):
    make_tree(tmp_path, {"a.txt": "a", "dir": {"b": "b"}})
    asked = []

    def prompt(question):
        asked.append(question)
        assert read_tree(tmp_path) == {"a.txt": "a", "dir": {"b": "b"}}
        return Answer.CANCEL

    job = run_job(JobKind.DELETE, [tmp_path / "a.txt", tmp_path / "dir"], prompt=prompt)

    assert [type(question) for question in asked] == [ConfirmDelete]
    assert len(asked[0].sources) == 2
    assert job.state is JobState.CANCELLED
    assert job.summary == "Cancelled after 0 of 2 items"
    assert read_tree(tmp_path) == {"a.txt": "a", "dir": {"b": "b"}}


def test_confirmed_delete_removes_a_nested_folder(tmp_path):
    make_tree(tmp_path, {"dir": {"a": "a", "sub": {"b": "b", "empty": {}}}, "keep": "k"})

    job = run_job(JobKind.DELETE, [tmp_path / "dir"], prompt=lambda question: Answer.DELETE)

    assert job.state is JobState.DONE
    assert job.summary == "Deleted 3 items"
    assert read_tree(tmp_path) == {"keep": "k"}


@needs_permissions
def test_a_failed_delete_names_the_file_and_keeps_its_folders(tmp_path):
    make_tree(tmp_path, {"dir": {"a": "a", "locked": {"stuck": "s"}, "z": "z"}})
    (tmp_path / "dir" / "locked").chmod(0o555)

    def prompt(question):
        return Answer.DELETE if isinstance(question, ConfirmDelete) else Answer.SKIP

    try:
        job = run_job(JobKind.DELETE, [tmp_path / "dir"], prompt=prompt)
    finally:
        (tmp_path / "dir" / "locked").chmod(0o755)

    assert job.state is JobState.FAILED
    assert never_done(job)
    assert [str(error) for error in job.errors] == ["stuck: permission denied"]
    assert job.error.path == str(tmp_path / "dir" / "locked" / "stuck")
    assert job.summary == "Deleted 2 of 3 files, 1 skipped"
    assert read_tree(tmp_path) == {"dir": {"locked": {"stuck": "s"}}}


def test_cancelled_delete_says_how_far_it_got(tmp_path):
    make_tree(tmp_path, {"dir": {"1": "", "2": "", "3": ""}})

    job = run_job(
        JobKind.DELETE,
        [tmp_path / "dir"],
        prompt=lambda question: Answer.DELETE,
        hook=lambda job: job.progress.files_done == 2 and job.cancel(),
    )

    assert job.state is JobState.CANCELLED
    assert job.summary == "Cancelled after 2 of 3 files"
    assert read_tree(tmp_path) == {"dir": {"3": ""}}
