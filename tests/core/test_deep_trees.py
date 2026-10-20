"""Trees deeper than Python lets a function call itself: nothing that goes through a
tree may do it by calling itself for each folder. The limit is lowered for the test,
so the tree needs no paths longer than a file system takes."""

import inspect
import os
import sys
import zipfile

import pytest
from helpers import gfile, run_job

from filehouse.core import archives
from filehouse.core.operations import JobKind, JobState
from filehouse.core.operations.archive import CompressJob

DEPTH = 300


@pytest.fixture
def deep(tmp_path):
    """A folder `DEPTH` folders deep with a file at the bottom, and a recursion limit
    well short of that depth from where the test runs."""
    parts = ["d"] * DEPTH
    bottom = tmp_path / "src" / os.path.join(*parts)
    bottom.mkdir(parents=True)
    (bottom / "end.txt").write_text("end")
    limit = sys.getrecursionlimit()
    sys.setrecursionlimit(len(inspect.stack()) + DEPTH // 3)
    try:
        yield tmp_path / "src", os.path.join(*parts, "end.txt")
    finally:
        sys.setrecursionlimit(limit)


def test_a_deep_tree_is_copied(tmp_path, deep):
    src, end = deep
    dst = tmp_path / "dst"
    dst.mkdir()

    job = run_job(JobKind.COPY, [src], dst)

    assert job.state is JobState.DONE, job.errors
    assert (dst / "src" / end).read_text() == "end"


def test_a_deep_tree_is_moved_and_can_be_taken_back(tmp_path, other_device, deep):
    # `deep` after `other_device`, so the limit is back before that folder is removed
    src, end = deep

    job = run_job(JobKind.MOVE, [src], other_device)

    assert job.state is JobState.DONE, job.errors
    assert (other_device / "src" / end).read_text() == "end"
    assert not src.exists()
    assert [(now.get_path(), was.get_path()) for now, was in job.undo.pairs] == [
        (str(other_device / "src"), str(src))
    ]


def test_a_deep_tree_is_deleted(deep):
    src, _end = deep

    job = run_job(JobKind.DELETE, [src], prompt=lambda question: "delete")

    assert job.state is JobState.DONE, job.errors
    assert not src.exists()


@pytest.mark.skipif(not archives.available(), reason="libarchive is not installed")
def test_a_deep_tree_is_compressed(tmp_path, deep):
    src, end = deep
    job = CompressJob([gfile(src)], gfile(tmp_path), "deep.zip", "zip")

    job.run()

    assert job.state is JobState.DONE, job.errors
    with zipfile.ZipFile(tmp_path / "deep.zip") as archive:
        assert archive.read(f"src/{end}") == b"end"
