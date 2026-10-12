"""Duplicate: a copy beside the source, under the name "Keep both" would give it."""

import os

from helpers import leftovers, make_tree, never_done, read_tree, run_job

from filehouse.core.operations import JobKind, JobState


def test_a_file_is_copied_beside_itself_under_a_free_name(tmp_path):
    make_tree(tmp_path, {"report.pdf": "pages", "other.txt": "o"})

    job = run_job(JobKind.DUPLICATE, [tmp_path / "report.pdf"])

    assert job.state is JobState.DONE
    assert job.summary == "Duplicated 1 file"
    assert read_tree(tmp_path) == {
        "report.pdf": "pages",
        "report (2).pdf": "pages",
        "other.txt": "o",
    }


def test_it_asks_nothing_and_replaces_nothing(tmp_path):
    make_tree(tmp_path, {"a.txt": "a", "a (2).txt": "mine", "a (3).txt": "mine too"})
    asked = []

    job = run_job(JobKind.DUPLICATE, [tmp_path / "a.txt"], prompt=asked.append)

    assert job.state is JobState.DONE and not asked
    assert read_tree(tmp_path) == {
        "a.txt": "a",
        "a (2).txt": "mine",
        "a (3).txt": "mine too",
        "a (4).txt": "a",
    }


def test_a_folder_is_copied_with_all_that_is_in_it(tmp_path):
    album = {"one.jpg": "1", "more": {"two.jpg": "2"}, "empty": {}}
    make_tree(tmp_path, {"album": album})

    job = run_job(JobKind.DUPLICATE, [tmp_path / "album"])

    assert job.state is JobState.DONE
    assert job.summary == "Duplicated 3 items"
    assert read_tree(tmp_path) == {"album": album, "album (2)": album}


def test_each_is_copied_into_the_folder_it_is_in(tmp_path):
    make_tree(tmp_path, {"a.txt": "a", "sub": {"deep": {"b.tar.gz": "b"}}})

    job = run_job(JobKind.DUPLICATE, [tmp_path / "a.txt", tmp_path / "sub" / "deep" / "b.tar.gz"])

    assert job.state is JobState.DONE
    assert read_tree(tmp_path) == {
        "a.txt": "a",
        "a (2).txt": "a",
        "sub": {"deep": {"b.tar.gz": "b", "b (2).tar.gz": "b"}},
    }


def test_a_link_is_duplicated_as_a_link(tmp_path):
    make_tree(tmp_path, {"file.txt": "f", "to-file": ("link", "file.txt")})

    job = run_job(JobKind.DUPLICATE, [tmp_path / "to-file"])

    assert job.state is JobState.DONE
    assert os.readlink(tmp_path / "to-file (2)") == "file.txt"
    assert read_tree(tmp_path)["file.txt"] == "f"


def test_cancel_mid_file_leaves_the_source_and_no_half_copy(tmp_path, slow_copy):
    make_tree(tmp_path, {"big.bin": "x" * 8000})

    def hook(job):
        if job.progress.bytes_done >= 2000:
            job.cancel()

    job = run_job(JobKind.DUPLICATE, [tmp_path / "big.bin"], hook=hook)

    assert job.state is JobState.CANCELLED
    assert job.summary == "Cancelled after 0 of 1 file"
    assert never_done(job)
    assert read_tree(tmp_path) == {"big.bin": "x" * 8000}
    assert not leftovers(tmp_path)


def test_a_failure_names_the_file_and_is_not_done(tmp_path):
    make_tree(tmp_path, {"there.txt": "t"})

    job = run_job(JobKind.DUPLICATE, [tmp_path / "gone.txt", tmp_path / "there.txt"])

    assert job.state is JobState.FAILED
    assert never_done(job)
    assert str(job.error) == "gone.txt: no such file or folder"
    assert read_tree(tmp_path) == {"there.txt": "t", "there (2).txt": "t"}


def test_what_failed_can_be_tried_again(tmp_path):
    make_tree(tmp_path, {"there.txt": "t"})
    job = run_job(JobKind.DUPLICATE, [tmp_path / "late.txt", tmp_path / "there.txt"])
    assert job.can_retry

    (tmp_path / "late.txt").write_text("l")
    again = job.retry()
    again.run()

    assert again.kind is JobKind.DUPLICATE and again.state is JobState.DONE
    assert read_tree(tmp_path) == {
        "there.txt": "t",
        "there (2).txt": "t",
        "late.txt": "l",
        "late (2).txt": "l",
    }
