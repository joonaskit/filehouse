import os

import pytest
from helpers import leftovers, make_tree, never_done, read_tree, run_job

from filehouse.core.operations import Answer, Failure, JobKind, JobState

TREE = {"a.txt": "aaa", "sub": {"b.txt": "bb", "deep": {"c.txt": "c"}}}

needs_permissions = pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permissions")


def test_same_device_move_renames_the_folder(tmp_path):
    src = make_tree(tmp_path / "src", TREE)
    dst = make_tree(tmp_path / "dst", {})
    inode = (src / "sub" / "b.txt").stat().st_ino

    job = run_job(JobKind.MOVE, [src], dst)

    assert job.state is JobState.DONE
    assert job.summary == "Moved 1 item"
    assert not src.exists()
    assert read_tree(dst) == {"src": TREE}
    assert (dst / "src" / "sub" / "b.txt").stat().st_ino == inode


def test_same_device_move_of_files(tmp_path):
    src = make_tree(tmp_path / "src", {"one": "1", "two": "2"})
    dst = make_tree(tmp_path / "dst", {})

    job = run_job(JobKind.MOVE, [src / "one", src / "two"], dst)

    assert job.summary == "Moved 2 files"
    assert read_tree(src) == {}
    assert read_tree(dst) == {"one": "1", "two": "2"}


def test_cross_device_move_of_a_nested_folder(tmp_path, other_device):
    src = make_tree(tmp_path / "src", TREE)
    (src / "a.txt").chmod(0o640)

    job = run_job(JobKind.MOVE, [src], other_device)

    assert job.state is JobState.DONE
    assert job.summary == "Moved 3 files"
    assert not src.exists()
    assert read_tree(other_device) == {"src": TREE}
    assert (other_device / "src" / "a.txt").stat().st_mode & 0o777 == 0o640
    assert job.progress.bytes_done == job.progress.bytes_total == 6
    assert not leftovers(tmp_path, other_device)


def assert_nothing_lost(src, dst, tree, prefix=()):
    """Every file is whole at the source, at the destination, or both. Returns how many moved."""
    moved = 0
    for name, value in tree.items():
        here, there = src.joinpath(*prefix, name), dst.joinpath(*prefix, name)
        if isinstance(value, dict):
            moved += assert_nothing_lost(src, dst, value, (*prefix, name))
            continue
        copies = [path for path in (here, there) if path.exists()]
        assert copies, f"{name} is gone"
        assert all(path.read_text() == value for path in copies), f"{name} is damaged"
        moved += not here.exists()
    return moved


def test_cross_device_move_cancelled_halfway_loses_nothing(tmp_path, other_device, slow_copy):
    tree = {"a.txt": "a" * 3000, "sub": {"b.txt": "b" * 3000, "c.txt": "c" * 3000}, "z.txt": "z"}
    src = make_tree(tmp_path / "src", tree)

    def cancel_in_the_second_file(job):
        if job.progress.files_done == 1 and job.progress.bytes_done > 4000:
            job.cancel()

    job = run_job(JobKind.MOVE, [src], other_device, hook=cancel_in_the_second_file)

    assert job.state is JobState.CANCELLED
    assert job.summary == "Cancelled after 1 of 4 files"
    assert assert_nothing_lost(src, other_device / "src", tree) == 1
    # The folder is split: what was moved is gone from the source, the rest is still there
    assert read_tree(src) == {"sub": tree["sub"], "z.txt": "z"}
    assert read_tree(other_device) == {"src": {"a.txt": tree["a.txt"], "sub": {}}}
    assert not leftovers(tmp_path, other_device)


@needs_permissions
def test_cross_device_move_failed_halfway_loses_nothing(tmp_path, other_device):
    tree = {"a.txt": "a", "sub": {"b.txt": "b", "locked.txt": "secret"}, "z.txt": "z"}
    src = make_tree(tmp_path / "src", tree)
    (src / "sub" / "locked.txt").chmod(0)

    job = run_job(JobKind.MOVE, [src], other_device)
    (src / "sub" / "locked.txt").chmod(0o600)

    assert job.state is JobState.FAILED
    assert never_done(job)
    assert job.summary == "Moved 3 of 4 files, 1 skipped"
    assert job.error.path == str(src / "sub" / "locked.txt")
    assert assert_nothing_lost(src, other_device / "src", tree) == 3
    # Folders that still hold a file are not removed
    assert read_tree(src) == {"sub": {"locked.txt": "secret"}}
    assert not leftovers(tmp_path, other_device)


def test_source_is_removed_only_after_its_copy_is_complete(tmp_path, other_device, slow_copy):
    src = tmp_path / "film"
    src.write_text("x" * 8000)
    seen = []

    def watch(job):
        if 0 < job.progress.bytes_done < 8000:
            seen.append((src.exists(), (other_device / "film").exists()))

    job = run_job(JobKind.MOVE, [src], other_device, hook=watch)

    assert job.state is JobState.DONE
    assert seen and all(source and not dest for source, dest in seen)
    assert not src.exists()
    assert (other_device / "film").read_text() == "x" * 8000


@needs_permissions
def test_a_source_that_cannot_be_removed_stays_and_is_reported(tmp_path, other_device):
    src = make_tree(tmp_path / "src", {"keep": "k"})
    src.chmod(0o555)
    asked = []

    def prompt(question):
        asked.append(question)
        return Answer.RETRY if len(asked) == 1 else Answer.SKIP

    try:
        job = run_job(JobKind.MOVE, [src / "keep"], other_device, prompt=prompt)
    finally:
        src.chmod(0o755)

    assert job.state is JobState.FAILED
    assert all(isinstance(question, Failure) for question in asked)
    assert len(asked) == 2
    assert str(job.error) == "keep: permission denied"
    # The copy was complete, so the retry only tried the removal again
    assert read_tree(src) == {"keep": "k"}
    assert read_tree(other_device) == {"keep": "k"}


def test_running_a_split_move_again_picks_up_the_rest(tmp_path, other_device, slow_copy):
    tree = {"a.txt": "a" * 3000, "b.txt": "b" * 3000}
    src = make_tree(tmp_path / "src", tree)
    first = run_job(
        JobKind.MOVE,
        [src],
        other_device,
        hook=lambda job: job.progress.files_done == 1 and job.cancel(),
    )
    assert first.state is JobState.CANCELLED
    assert read_tree(src) == {"b.txt": tree["b.txt"]}

    again = run_job(JobKind.MOVE, [src], other_device, prompt=lambda question: Answer.REPLACE)

    assert again.state is JobState.DONE
    assert not src.exists()
    assert read_tree(other_device) == {"src": tree}
