import os

import pytest
from gi.repository import Gio

from filehouse.core.listing import (
    Count,
    ListingError,
    count_children,
    list_directory,
    mode_text,
    natural_key,
)


def listed(path, **kwargs):
    batches = []
    list_directory(Gio.File.new_for_path(str(path)), batches.append, **kwargs)
    return batches


def by_name(batches):
    return {info.name: info for batch in batches for info in batch}


def test_lists_files_and_folders(tmp_path):
    (tmp_path / "notes.txt").write_text("hello")
    (tmp_path / "sub").mkdir()

    infos = by_name(listed(tmp_path))

    assert set(infos) == {"notes.txt", "sub"}
    assert infos["sub"].is_dir
    assert not infos["notes.txt"].is_dir
    assert infos["notes.txt"].size == 5
    assert infos["notes.txt"].content_type == "text/plain"
    assert infos["notes.txt"].modified == int((tmp_path / "notes.txt").stat().st_mtime)


def test_hidden_and_backup_files_are_marked(tmp_path):
    for name in (".config", "draft.txt~", "visible"):
        (tmp_path / name).write_text("")

    infos = by_name(listed(tmp_path))

    assert infos[".config"].is_hidden
    assert infos["draft.txt~"].is_hidden
    assert not infos["visible"].is_hidden


def test_symlink_to_a_folder_is_a_folder(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "link").symlink_to(tmp_path / "real")
    (tmp_path / "broken").symlink_to(tmp_path / "missing")

    infos = by_name(listed(tmp_path))

    assert infos["link"].is_dir and infos["link"].is_symlink
    assert not infos["broken"].is_dir and infos["broken"].is_symlink


def test_entries_arrive_in_batches(tmp_path):
    for index in range(25):
        (tmp_path / f"file{index}").write_text("")

    batches = listed(tmp_path, batch_size=10)

    assert [len(batch) for batch in batches] == [10, 10, 5]
    assert len(by_name(batches)) == 25


def test_empty_folder_gives_no_batches(tmp_path):
    assert listed(tmp_path) == []


def test_missing_folder_raises(tmp_path):
    with pytest.raises(ListingError):
        listed(tmp_path / "missing")


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read everything")
def test_unreadable_folder_raises_with_a_reason(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        with pytest.raises(ListingError, match="(?i)permission"):
            listed(locked)
    finally:
        locked.chmod(0o700)


def test_cancelled_listing_returns_quietly(tmp_path):
    (tmp_path / "file").write_text("")
    cancellable = Gio.Cancellable()
    cancellable.cancel()

    assert listed(tmp_path, cancellable=cancellable) == []


def test_natural_key_orders_numbers_by_value_and_ignores_case():
    names = ["file10.txt", "File2.txt", "file1.txt", "apple", "Banana", "10", "9"]

    assert sorted(names, key=natural_key) == [
        "9",
        "10",
        "apple",
        "Banana",
        "file1.txt",
        "File2.txt",
        "file10.txt",
    ]


def test_permission_bits_are_listed(tmp_path):
    (tmp_path / "script.sh").write_text("")
    (tmp_path / "script.sh").chmod(0o750)
    (tmp_path / "private").mkdir(mode=0o700)

    infos = by_name(listed(tmp_path))

    assert infos["script.sh"].mode == 0o750
    assert infos["private"].mode == 0o700


def test_mode_text_reads_like_ls():
    assert mode_text(0o755) == "rwxr-xr-x"
    assert mode_text(0o640) == "rw-r-----"
    assert mode_text(0o1777) == "rwxrwxrwt"
    assert mode_text(None) == ""


def test_count_children_tells_hidden_entries_apart(tmp_path):
    for name in ("a", "b", ".hidden", "draft~"):
        (tmp_path / name).write_text("")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deeper").write_text("")

    # Only what is directly in the folder
    assert count_children(Gio.File.new_for_path(str(tmp_path))) == Count(shown=3, hidden=2)
    assert count_children(Gio.File.new_for_path(str(tmp_path / "sub"))) == Count(1, 0)


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read anything")
def test_count_children_of_an_unreadable_folder_is_unknown(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        assert count_children(Gio.File.new_for_path(str(locked))) is None
    finally:
        locked.chmod(0o700)


def test_count_children_stops_when_cancelled(tmp_path):
    (tmp_path / "a").write_text("")
    cancellable = Gio.Cancellable()
    cancellable.cancel()

    assert count_children(Gio.File.new_for_path(str(tmp_path)), cancellable) is None
