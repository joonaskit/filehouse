"""Copies to a folder that cannot rename: the file is written under its real name."""

import pytest
from helpers import leftovers, make_tree, not_supported, read_tree, run_job

from filehouse.core.operations import Answer, JobKind, JobState, copy


@pytest.fixture
def no_rename(monkeypatch):
    monkeypatch.setattr(copy, "_rename_item", not_supported)
    monkeypatch.setattr(copy, "_CANNOT_RENAME", set())


def test_copy_without_rename(tmp_path, no_rename):
    src = make_tree(tmp_path / "src", {"a.txt": "a", "b.txt": "b"})
    dst = make_tree(tmp_path / "dst", {})

    job = run_job(JobKind.COPY, [src / "a.txt", src / "b.txt"], dst)

    assert job.state is JobState.DONE
    assert read_tree(dst) == {"a.txt": "a", "b.txt": "b"}
    assert not leftovers(tmp_path)
    # Found out once, then not tried again for the rest of the folder
    assert len(copy._CANNOT_RENAME) == 1


def test_replace_without_rename(tmp_path, no_rename):
    src = make_tree(tmp_path / "src", {"a.txt": "new"})
    dst = make_tree(tmp_path / "dst", {"a.txt": "old"})

    job = run_job(JobKind.COPY, [src / "a.txt"], dst, prompt=lambda q: Answer.REPLACE)

    assert job.state is JobState.DONE
    assert read_tree(dst) == {"a.txt": "new"}
    assert not leftovers(tmp_path)


def test_conflict_is_not_overwritten_without_rename(tmp_path, no_rename):
    src = make_tree(tmp_path / "src", {"a.txt": "new"})
    dst = make_tree(tmp_path / "dst", {"a.txt": "old"})

    job = run_job(JobKind.COPY, [src / "a.txt"], dst, prompt=lambda q: Answer.SKIP)

    assert job.state is JobState.DONE
    assert read_tree(dst) == {"a.txt": "old"}


def test_cancel_without_rename_removes_only_the_partial_file(tmp_path, no_rename, slow_copy):
    src = make_tree(tmp_path / "src", {"1.txt": "a" * 4000, "2.txt": "b" * 4000})
    dst = make_tree(tmp_path / "dst", {"mine.txt": "keep me"})
    seen = {}

    def hook(job):
        seen.setdefault("start", job.progress.bytes_done)
        if job.progress.files_done == 1 and job.progress.bytes_done - seen["start"] >= 5000:
            job.cancel()

    job = run_job(JobKind.COPY, [src / "1.txt", src / "2.txt"], dst, hook=hook)

    assert job.state is JobState.CANCELLED
    assert read_tree(dst) == {"mine.txt": "keep me", "1.txt": "a" * 4000}
    assert not leftovers(tmp_path)
