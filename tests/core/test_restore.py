"""Restore, and deleting what is in the trash, with a plain folder standing in for the trash.

The real trash is a GVFS location these tests must not touch. What they cover is the
engine: where things go, what happens when something is in the way, and what is reported.
"""

import os

import pytest
from helpers import make_tree, never_done, read_tree, run_job

from filehouse.core import locations
from filehouse.core.operations import Answer, ConfirmDelete, Conflict, JobKind, JobState


@pytest.fixture
def trashed(tmp_path):
    """A stand-in trash holding a file and a folder, and the place they came from."""
    bin_ = make_tree(
        tmp_path / "bin", {"report.pdf": "r", "album": {"a.jpg": "a", "in": {"b": "b"}}}
    )
    home = make_tree(tmp_path / "home", {"Documents": {}})
    return bin_, home


def test_restore_puts_items_back_where_they_were(trashed):
    bin_, home = trashed

    job = run_job(
        JobKind.RESTORE,
        [bin_ / "report.pdf", bin_ / "album"],
        targets=[home / "Documents" / "report.pdf", home / "Pictures" / "2024" / "album"],
    )

    assert job.state is JobState.DONE
    assert job.summary == "Restored 2 items"
    assert read_tree(bin_) == {}
    # The folder an item was in is made again when it is gone
    assert read_tree(home) == {
        "Documents": {"report.pdf": "r"},
        "Pictures": {"2024": {"album": {"a.jpg": "a", "in": {"b": "b"}}}},
    }


def test_restore_under_another_name(trashed):
    # In the trash an item can have a different name than it had
    bin_, home = trashed

    job = run_job(JobKind.RESTORE, [bin_ / "report.pdf"], targets=[home / "Documents" / "old.pdf"])

    assert job.state is JobState.DONE
    assert read_tree(home / "Documents") == {"old.pdf": "r"}


def test_restore_never_replaces_what_is_there_now(trashed):
    bin_, home = trashed
    (home / "Documents" / "report.pdf").write_text("newer")
    asked = []

    def prompt(question):
        asked.append(question)
        return Answer.REPLACE if isinstance(question, Conflict) else Answer.SKIP

    job = run_job(
        JobKind.RESTORE,
        [bin_ / "report.pdf"],
        targets=[home / "Documents" / "report.pdf"],
        prompt=prompt,
    )

    assert isinstance(asked[0], Conflict)
    assert not asked[0].can_replace
    assert Answer.REPLACE not in asked[0].answers
    assert job.state is JobState.FAILED
    assert never_done(job)
    assert str(job.error) == "report.pdf: already exists where it was"
    assert (home / "Documents" / "report.pdf").read_text() == "newer"
    assert (bin_ / "report.pdf").read_text() == "r"


def test_restore_can_keep_both_or_skip(trashed):
    bin_, home = trashed
    (home / "Documents" / "report.pdf").write_text("newer")
    (home / "Documents" / "album").mkdir()
    replies = {"report.pdf": Answer.KEEP_BOTH, "album": Answer.SKIP}

    job = run_job(
        JobKind.RESTORE,
        [bin_ / "report.pdf", bin_ / "album"],
        targets=[home / "Documents" / "report.pdf", home / "Documents" / "album"],
        prompt=lambda question: replies[question.source.get_basename()],
    )

    assert job.state is JobState.DONE
    assert job.summary == "Restored 1 item"
    assert read_tree(home / "Documents") == {
        "report.pdf": "newer",
        "report (2).pdf": "r",
        "album": {},
    }
    assert list(read_tree(bin_)) == ["album"]


def test_restore_without_a_prompt_keeps_both_sides(trashed):
    bin_, home = trashed
    (home / "Documents" / "report.pdf").write_text("newer")

    job = run_job(
        JobKind.RESTORE, [bin_ / "report.pdf"], targets=[home / "Documents" / "report.pdf"]
    )

    assert job.state is JobState.FAILED
    assert (home / "Documents" / "report.pdf").read_text() == "newer"
    assert (bin_ / "report.pdf").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permissions")
def test_a_failed_restore_names_the_item_and_can_be_retried(trashed):
    bin_, home = trashed
    (home / "Documents").chmod(0o555)

    job = run_job(
        JobKind.RESTORE,
        [bin_ / "report.pdf", bin_ / "album"],
        targets=[home / "Documents" / "report.pdf", home / "album"],
    )
    (home / "Documents").chmod(0o755)

    assert job.state is JobState.FAILED
    assert job.summary == "Restored 1 of 2 items, 1 skipped"
    assert str(job.error) == "report.pdf: permission denied"
    assert job.error.step == "restore"
    assert (bin_ / "report.pdf").exists()

    again = job.retry()
    again.run()

    assert again.state is JobState.DONE
    assert read_tree(bin_) == {}
    assert (home / "Documents" / "report.pdf").read_text() == "r"


def test_a_missing_trashed_item_fails_and_names_it(trashed):
    bin_, home = trashed

    job = run_job(JobKind.RESTORE, [bin_ / "gone"], targets=[home / "gone"])

    assert job.state is JobState.FAILED
    assert str(job.error) == "gone: no such file or folder"


def test_cancelled_restore_says_how_far_it_got(trashed):
    bin_, home = trashed

    job = run_job(
        JobKind.RESTORE,
        [bin_ / "report.pdf", bin_ / "album"],
        targets=[home / "report.pdf", home / "album"],
        hook=lambda job: job.progress.files_done == 1 and job.cancel(),
    )

    assert job.state is JobState.CANCELLED
    assert job.summary == "Cancelled after 1 of 2 items"
    assert list(read_tree(bin_)) == ["album"]


def test_locations_know_the_trash():
    trash = locations.trash()

    assert locations.is_trash(trash) and locations.in_trash(trash)
    assert locations.in_trash(trash.get_child("report.pdf"))
    assert not locations.is_trash(trash.get_child("report.pdf"))
    assert not locations.in_trash(locations.home())
    assert locations.display_name(trash) == "Trash"
    assert [name for name, _ in locations.segments(trash.get_child("album"))] == ["Trash", "album"]


def test_an_item_in_the_trash_is_deleted_in_one_step(trashed, monkeypatch):
    bin_, _home = trashed
    monkeypatch.setattr(locations, "in_trash", lambda location: True)
    deleted = []

    def delete_whole(step, cancellable):
        # As the trash does: the item goes with everything in it
        deleted.append((step.source.get_basename(), step.whole))
        return True

    from filehouse.core.operations import delete

    monkeypatch.setattr(delete, "delete", delete_whole)
    asked = []

    def prompt(question):
        asked.append(question)
        return Answer.DELETE

    job = run_job(JobKind.DELETE, [bin_ / "report.pdf", bin_ / "album"], prompt=prompt)

    # Nothing inside the folder was planned by itself, and it still asked first
    assert deleted == [("report.pdf", True), ("album", True)]
    assert [type(question) for question in asked] == [ConfirmDelete]
    assert job.state is JobState.DONE
    assert job.summary == "Deleted 2 items"


def test_a_whole_delete_that_cannot_be_done_in_one_go_is_done_file_by_file(trashed, monkeypatch):
    bin_, _home = trashed
    monkeypatch.setattr(locations, "in_trash", lambda location: True)

    job = run_job(JobKind.DELETE, [bin_ / "album"], prompt=lambda question: Answer.DELETE)

    assert job.state is JobState.DONE
    assert read_tree(bin_) == {"report.pdf": "r"}
