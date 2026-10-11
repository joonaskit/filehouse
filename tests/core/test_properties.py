import os

import pytest
from gi.repository import Gio
from helpers import gfile, make_tree

from filehouse.core import properties
from filehouse.core.errors import OperationError


def test_reads_a_file(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("hello")
    path.chmod(0o640)
    os.utime(path, (1_500_000_000, 1_000_000_000))

    item = properties.read(gfile(path))

    assert item.name == "notes.txt"
    assert item.folder == str(tmp_path)
    assert item.kind == "Plain text document"
    assert item.content_type == "text/plain"
    assert not item.is_dir and item.link_target is None
    assert item.size == 5
    assert item.modified == 1_000_000_000
    assert item.accessed == 1_500_000_000
    assert item.mode == 0o640
    assert item.owner
    assert item.original_path is None


def test_reads_a_folder_and_a_link_without_following_it(tmp_path):
    make_tree(tmp_path, {"dir": {"f": "x"}, "link": ("link", "dir"), "broken": ("link", "nowhere")})

    assert properties.read(gfile(tmp_path / "dir")).is_dir
    link = properties.read(gfile(tmp_path / "link"))
    assert link.link_target == "dir" and not link.is_dir
    assert properties.read(gfile(tmp_path / "broken")).link_target == "nowhere"


def test_a_missing_file_raises_and_names_it(tmp_path):
    with pytest.raises(OperationError) as raised:
        properties.read(gfile(tmp_path / "gone"))

    assert str(raised.value) == "gone: no such file or folder"


def test_measure_adds_up_a_tree_without_following_links(tmp_path):
    outside = make_tree(tmp_path / "outside", {"huge": "x" * 5000})
    root = make_tree(
        tmp_path / "root",
        {"a": "x" * 10, "sub": {"b": "x" * 20, "deep": {"c": "x" * 30}}, "out": ("link", outside)},
    )

    totals = properties.measure([gfile(root)])

    # The link counts as a file of no size; what it points to is not counted
    assert totals == properties.Totals(size=60, files=4, folders=3)


def test_measure_of_several_items_and_of_a_single_file(tmp_path):
    make_tree(tmp_path, {"a": "x" * 10, "dir": {"b": "x" * 5}})

    assert properties.measure([gfile(tmp_path / "a")]) == properties.Totals(size=10, files=1)
    both = properties.measure([gfile(tmp_path / "a"), gfile(tmp_path / "dir")])
    assert both == properties.Totals(size=15, files=2, folders=1)


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read everything")
def test_measure_says_when_it_could_not_look_everywhere(tmp_path):
    root = make_tree(tmp_path / "root", {"a": "x" * 10, "locked": {"b": "x" * 99}})
    (root / "locked").chmod(0)
    try:
        totals = properties.measure([gfile(root)])
    finally:
        (root / "locked").chmod(0o755)

    assert totals == properties.Totals(size=10, files=1, folders=2, unreadable=1)


def test_measure_reports_progress_and_can_be_cancelled(tmp_path, monkeypatch):
    monkeypatch.setattr(properties, "_REPORT_EVERY", 10)
    make_tree(tmp_path, {f"file{index}": "x" for index in range(35)})
    seen = []

    totals = properties.measure([gfile(tmp_path)], on_progress=seen.append)

    assert totals.files == 35
    assert [progress.files for progress in seen] == [10, 20, 30]

    cancellable = Gio.Cancellable()
    cancellable.cancel()
    assert properties.measure([gfile(tmp_path)], cancellable) is None


def test_permissions_in_words():
    assert properties.permissions(0o640, is_dir=False) == ("Read, write", "Read", "Nothing")
    assert properties.permissions(0o755, is_dir=False) == (
        "Read, write, run",
        "Read, run",
        "Read, run",
    )
    assert properties.permissions(0o700, is_dir=True) == ("Read, write, open", "Nothing", "Nothing")
