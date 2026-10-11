import os

import pytest
from helpers import leftovers, make_tree, read_tree, run_job

from filehouse.core.operations import JobKind, JobState

TREE = {"a.txt": "aaa", "sub": {"b.txt": "bb", "deep": {"c.txt": "c"}}}


def test_copies_a_nested_folder(tmp_path):
    src = make_tree(tmp_path / "src", TREE)
    dst = make_tree(tmp_path / "dst", {})

    job = run_job(JobKind.COPY, [src], dst)

    assert job.state is JobState.DONE
    assert job.summary == "Copied 3 files"
    assert read_tree(dst) == {"src": TREE}
    assert read_tree(src) == TREE
    assert not leftovers(tmp_path)


def test_states_run_from_planning_to_done(tmp_path):
    src = make_tree(tmp_path / "src", TREE)

    job = run_job(JobKind.COPY, [src], make_tree(tmp_path / "dst", {}))

    assert job.seen.states == [JobState.PLANNING, JobState.RUNNING, JobState.DONE]


def test_progress_is_indeterminate_while_planning_and_then_counts_bytes(tmp_path):
    src = make_tree(tmp_path / "src", {"big": "x" * 5000, "small": "y" * 10, "sub": {"z": "zz"}})

    job = run_job(JobKind.COPY, [src], make_tree(tmp_path / "dst", {}))

    assert job.seen.progress[0].fraction is None
    planned = [progress for progress in job.seen.progress if progress.planned]
    assert all(progress.bytes_total == 5012 for progress in planned)
    done = [progress.bytes_done for progress in planned]
    assert done == sorted(done)
    assert job.progress.bytes_done == 5012
    assert job.progress.fraction == 1.0
    assert (job.progress.files_done, job.progress.files_total) == (3, 3)


def test_progress_moves_inside_one_large_file(tmp_path, slow_copy):
    src = tmp_path / "big"
    src.write_bytes(b"x" * 10_000)

    job = run_job(JobKind.COPY, [src], make_tree(tmp_path / "dst", {}))

    partial = {p.bytes_done for p in job.seen.progress if 0 < p.bytes_done < 10_000}
    assert len(partial) > 3
    assert (tmp_path / "dst" / "big").read_bytes() == src.read_bytes()


def test_copy_keeps_modification_time_and_permissions(tmp_path):
    src = make_tree(tmp_path / "src", {"script.sh": "#!/bin/sh\n", "sub": {"f": "x"}})
    (src / "script.sh").chmod(0o750)
    os.utime(src / "script.sh", (1_000_000_000, 1_000_000_000))
    (src / "sub").chmod(0o700)
    os.utime(src / "sub", (1_100_000_000, 1_100_000_000))
    dst = make_tree(tmp_path / "dst", {})

    job = run_job(JobKind.COPY, [src], dst)

    assert job.state is JobState.DONE
    copied = (dst / "src" / "script.sh").stat()
    assert copied.st_mode & 0o777 == 0o750
    assert copied.st_mtime == 1_000_000_000
    folder = (dst / "src" / "sub").stat()
    assert folder.st_mode & 0o777 == 0o700
    assert folder.st_mtime == 1_100_000_000


def test_copies_several_sources(tmp_path):
    src = make_tree(tmp_path / "src", {"one": "1", "two": "2", "dir": {"three": "3"}})
    dst = make_tree(tmp_path / "dst", {})

    job = run_job(JobKind.COPY, [src / "one", src / "two", src / "dir"], dst)

    assert job.summary == "Copied 3 files"
    assert read_tree(dst) == {"one": "1", "two": "2", "dir": {"three": "3"}}


def test_an_empty_folder_counts_as_an_item(tmp_path):
    src = make_tree(tmp_path / "empty", {})
    dst = make_tree(tmp_path / "dst", {})

    job = run_job(JobKind.COPY, [src], dst)

    assert job.state is JobState.DONE
    assert job.summary == "Copied 1 item"
    assert read_tree(dst) == {"empty": {}}


@pytest.mark.parametrize("kind", [JobKind.COPY, JobKind.MOVE])
@pytest.mark.parametrize("inside", ["", "sub", "sub/deep"])
def test_a_folder_into_itself_fails_during_planning(tmp_path, kind, inside):
    src = make_tree(tmp_path / "src", TREE)

    job = run_job(kind, [src], src / inside)

    assert job.state is JobState.FAILED
    assert JobState.RUNNING not in job.seen.states
    assert job.error.path == str(src)
    assert "into itself" in str(job.error)
    assert job.summary == str(job.error)
    assert read_tree(src) == TREE


def test_a_folder_into_itself_through_a_link_fails_too(tmp_path):
    src = make_tree(tmp_path / "src", TREE)
    (tmp_path / "shortcut").symlink_to(src / "sub")

    job = run_job(JobKind.COPY, [src], tmp_path / "shortcut")

    assert job.state is JobState.FAILED
    assert read_tree(src) == TREE


def test_a_missing_source_fails_and_names_it(tmp_path):
    dst = make_tree(tmp_path / "dst", {})
    (tmp_path / "there").write_text("x")

    job = run_job(JobKind.COPY, [tmp_path / "gone", tmp_path / "there"], dst)

    assert job.state is JobState.FAILED
    assert job.error.path == str(tmp_path / "gone")
    assert str(job.error) == "gone: no such file or folder"
    assert read_tree(dst) == {"there": "x"}
