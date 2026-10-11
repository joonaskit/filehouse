import os

import pytest
from helpers import make_tree, not_supported, read_tree, run_job

from filehouse.core.operations import Answer, JobKind, JobState, copy


@pytest.fixture
def linked(tmp_path):
    """A folder holding one link of every sort, and the things they point to, outside it."""
    target = make_tree(tmp_path / "target", {"file.txt": "the target", "dir": {"inside.txt": "in"}})
    src = make_tree(
        tmp_path / "src",
        {
            "plain.txt": "plain",
            "to-file": ("link", target / "file.txt"),
            "to-dir": ("link", target / "dir"),
            "broken": ("link", "does/not/exist"),
            "to-parent": ("link", ".."),
        },
    )
    for path in (target / "file.txt", target / "dir", target / "dir" / "inside.txt"):
        os.utime(path, (1_000_000_000, 1_000_000_000))
    return src, target


LINKS = {
    "to-file": lambda target: ("link", str(target / "file.txt")),
    "to-dir": lambda target: ("link", str(target / "dir")),
    "broken": lambda target: ("link", "does/not/exist"),
    "to-parent": lambda target: ("link", ".."),
}


def expected(target):
    return {"plain.txt": "plain"} | {name: link(target) for name, link in LINKS.items()}


def assert_target_untouched(target):
    assert read_tree(target) == {"file.txt": "the target", "dir": {"inside.txt": "in"}}
    for path in (target / "file.txt", target / "dir", target / "dir" / "inside.txt"):
        assert path.stat().st_mtime == 1_000_000_000


@pytest.mark.parametrize("kind", [JobKind.COPY, JobKind.MOVE])
@pytest.mark.parametrize("cross_device", [False, True])
def test_links_are_copied_as_links(tmp_path, monkeypatch, linked, kind, cross_device):
    src, target = linked
    if cross_device:
        monkeypatch.setattr(copy, "_rename_item", not_supported)
    dst = make_tree(tmp_path / "dst", {})

    job = run_job(kind, [src], dst)

    assert job.state is JobState.DONE
    assert read_tree(dst) == {"src": expected(target)}
    assert_target_untouched(target)
    if kind is JobKind.MOVE:
        assert not src.exists()
    else:
        assert read_tree(src) == expected(target)
    if kind is JobKind.COPY or cross_device:
        # One step per link: the planner did not walk into the linked folders
        assert job.progress.files_total == 5
        assert job.progress.bytes_total == len("plain")


@pytest.mark.parametrize("kind", [JobKind.COPY, JobKind.MOVE])
@pytest.mark.parametrize("name", LINKS)
def test_each_link_as_the_item_itself(tmp_path, real_other_device, linked, kind, name):
    src, target = linked

    job = run_job(kind, [src / name], real_other_device)

    assert job.state is JobState.DONE
    assert job.progress.files_total == 1
    assert read_tree(real_other_device) == {name: LINKS[name](target)}
    assert (src / name).is_symlink() == (kind is JobKind.COPY)
    assert_target_untouched(target)


def test_replacing_a_link_replaces_the_link_not_its_target(tmp_path, linked):
    src, target = linked
    dst = make_tree(tmp_path / "dst", {"plain.txt": ("link", target / "file.txt")})

    job = run_job(JobKind.COPY, [src / "plain.txt"], dst, prompt=lambda question: Answer.REPLACE)

    assert job.state is JobState.DONE
    assert read_tree(dst) == {"plain.txt": "plain"}
    assert_target_untouched(target)


def test_delete_removes_the_link_not_its_target(linked):
    src, target = linked

    job = run_job(JobKind.DELETE, [src], prompt=lambda question: Answer.DELETE)

    assert job.state is JobState.DONE
    assert job.summary == "Deleted 5 files"
    assert not src.exists()
    assert_target_untouched(target)
