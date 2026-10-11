import os

import pytest
from gi.repository import Gio

from filehouse.core.listing import ListingError, list_directory, natural_key


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
