import os

import pytest
from gi.repository import Gio
from PySide6.QtCore import Qt

from filehouse.qt_models.directory_model import (
    MODIFIED,
    NAME,
    PERMISSIONS,
    SIZE,
    DirectoryModel,
    DirectorySortProxy,
)


@pytest.fixture
def model(qtbot):
    model = DirectoryModel()
    yield model
    model.close()


def load(qtbot, model, path):
    with qtbot.waitSignal(model.loaded, timeout=5000):
        model.set_location(Gio.File.new_for_path(str(path)))


def names(model_or_proxy):
    return [model_or_proxy.index(row, NAME).data() for row in range(model_or_proxy.rowCount())]


def test_lists_a_folder(qtbot, model, tmp_path):
    (tmp_path / "a.txt").write_text("12345")
    (tmp_path / "a.txt").chmod(0o640)
    (tmp_path / "sub").mkdir()

    load(qtbot, model, tmp_path)

    assert sorted(names(model)) == ["a.txt", "sub"]
    assert [model.headerData(c, Qt.Orientation.Horizontal) for c in range(model.columnCount())] == [
        "Name",
        "Permissions",
        "Modified",
        "Size",
        "Type",
        "Created",
        "Accessed",
        "Owner",
        "Group",
        "Original location",
        "Deleted",
    ]
    assert not model.loading
    row = model.row_for_name("a.txt")
    assert model.index(row, SIZE).data() != ""
    assert model.index(row, MODIFIED).data().startswith("Today, ")
    assert model.index(row, PERMISSIONS).data() == "rw-r-----"


def test_large_folder_loads_completely(qtbot, model, tmp_path):
    for index in range(1000):
        (tmp_path / f"file{index}").write_text("")

    load(qtbot, model, tmp_path)

    assert model.rowCount() == 1000


def test_missing_folder_reports_failure(qtbot, model, tmp_path):
    with qtbot.waitSignal(model.load_failed, timeout=5000) as failed:
        model.set_location(Gio.File.new_for_path(str(tmp_path / "missing")))

    assert failed.args[0]
    assert model.rowCount() == 0


def test_changing_folder_drops_the_old_entries(qtbot, model, tmp_path):
    (tmp_path / "one").mkdir()
    (tmp_path / "one" / "old").write_text("")
    (tmp_path / "two").mkdir()
    (tmp_path / "two" / "new").write_text("")

    load(qtbot, model, tmp_path / "one")
    load(qtbot, model, tmp_path / "two")

    assert names(model) == ["new"]


def test_follows_files_created_renamed_and_deleted_elsewhere(qtbot, model, tmp_path):
    (tmp_path / "old").write_text("")
    load(qtbot, model, tmp_path)

    (tmp_path / "created").write_text("")
    qtbot.waitUntil(lambda: model.row_for_name("created") >= 0, timeout=5000)

    (tmp_path / "created").rename(tmp_path / "renamed")
    qtbot.waitUntil(lambda: model.row_for_name("renamed") >= 0, timeout=5000)
    assert model.row_for_name("created") == -1

    (tmp_path / "old").unlink()
    qtbot.waitUntil(lambda: model.row_for_name("old") == -1, timeout=5000)
    assert names(model) == ["renamed"]


def test_file_changed_elsewhere_updates_its_size(qtbot, model, tmp_path):
    (tmp_path / "grow").write_text("")
    load(qtbot, model, tmp_path)

    (tmp_path / "grow").write_text("x" * 4096)

    qtbot.waitUntil(lambda: model.info(0).size == 4096, timeout=5000)
    assert model.rowCount() == 1


def test_proxy_sorts_folders_first_then_naturally(qtbot, model, tmp_path):
    for name in ("file10", "File2", "file1", ".hidden"):
        (tmp_path / name).write_text("")
    (tmp_path / "zeta").mkdir()
    (tmp_path / "Alpha").mkdir()
    proxy = DirectorySortProxy()
    proxy.setSourceModel(model)
    proxy.sort(NAME, Qt.SortOrder.AscendingOrder)

    load(qtbot, model, tmp_path)

    assert names(proxy) == ["Alpha", "zeta", "file1", "File2", "file10"]

    proxy.sort(NAME, Qt.SortOrder.DescendingOrder)
    assert names(proxy) == ["zeta", "Alpha", "file10", "File2", "file1"]

    proxy.set_show_hidden(True)
    assert names(proxy) == ["zeta", "Alpha", "file10", "File2", "file1", ".hidden"]


def test_proxy_sorts_by_size(qtbot, model, tmp_path):
    (tmp_path / "big").write_text("x" * 300)
    (tmp_path / "small").write_text("x")
    (tmp_path / "medium").write_text("x" * 20)
    proxy = DirectorySortProxy()
    proxy.setSourceModel(model)
    proxy.sort(SIZE, Qt.SortOrder.AscendingOrder)

    load(qtbot, model, tmp_path)

    assert names(proxy) == ["small", "medium", "big"]


def test_a_file_renamed_right_after_it_is_created_leaves_no_ghost(qtbot, model, tmp_path):
    load(qtbot, model, tmp_path)
    answers = []
    on_info = model._on_info
    # Hold back the answers about files, as a busy system would
    model._on_info = lambda *args: answers.append(args)
    (tmp_path / ".part").write_text("x")
    qtbot.waitUntil(lambda: len(answers) >= 1, timeout=5000)
    (tmp_path / ".part").rename(tmp_path / "final.txt")
    qtbot.waitUntil(lambda: any(a[0].get_basename() == "final.txt" for a in answers), timeout=5000)

    for args in answers:
        on_info(*args)

    assert names(model) == ["final.txt"]


def size_text(model, name):
    return model.index(model.row_for_name(name), SIZE).data()


def test_folders_show_how_many_items_they_hold_once_counted(qtbot, model, tmp_path):
    (tmp_path / "empty").mkdir()
    (tmp_path / "one").mkdir()
    (tmp_path / "one" / "a").write_text("")
    (tmp_path / "three").mkdir()
    for name in ("a", "b", ".hidden"):
        (tmp_path / "three" / name).write_text("")
    (tmp_path / "three" / "sub").mkdir()
    (tmp_path / "three" / "sub" / "not counted").write_text("")

    load(qtbot, model, tmp_path)

    qtbot.waitUntil(lambda: size_text(model, "three") != "", timeout=5000)
    assert size_text(model, "empty") == "0 items"
    assert size_text(model, "one") == "1 item"
    assert size_text(model, "three") == "3 items"

    # With hidden files on show, they are part of the count
    proxy = DirectorySortProxy()
    proxy.setSourceModel(model)
    proxy.set_show_hidden(True)
    assert size_text(model, "three") == "4 items"


def test_the_listing_does_not_wait_for_the_counts(qtbot, model, tmp_path):
    for index in range(300):
        (tmp_path / f"folder{index}").mkdir()
        (tmp_path / f"folder{index}" / "file").write_text("")

    load(qtbot, model, tmp_path)

    # Listed in full, and no count has reached this thread yet
    assert model.rowCount() == 300
    assert model._counts == {}
    qtbot.waitUntil(lambda: len(model._counts) == 300, timeout=5000)
    assert size_text(model, "folder299") == "1 item"
    qtbot.waitUntil(lambda: not model._counting, timeout=5000)


def test_a_folder_made_later_is_counted_and_a_recount_sees_what_changed(
    qtbot, model, tmp_path, monkeypatch
):
    monkeypatch.setattr("filehouse.qt_models.directory_model._COUNT_DELAY_MS", 0)
    model._count_timer.setInterval(0)
    load(qtbot, model, tmp_path)

    (tmp_path / "new").mkdir()
    qtbot.waitUntil(lambda: model.row_for_name("new") >= 0, timeout=5000)
    qtbot.waitUntil(lambda: size_text(model, "new") == "0 items", timeout=5000)

    (tmp_path / "new" / "a").write_text("")
    qtbot.waitUntil(lambda: not model._counting, timeout=5000)
    model.recount()
    qtbot.waitUntil(lambda: size_text(model, "new") == "1 item", timeout=5000)


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read anything")
def test_a_folder_that_cannot_be_read_shows_no_count(qtbot, model, tmp_path):
    (tmp_path / "locked").mkdir()
    (tmp_path / "open").mkdir()
    (tmp_path / "locked").chmod(0)
    try:
        load(qtbot, model, tmp_path)
        qtbot.waitUntil(lambda: size_text(model, "open") == "0 items", timeout=5000)
        assert size_text(model, "locked") == ""
        assert model.index(model.row_for_name("locked"), PERMISSIONS).data() == "---------"
    finally:
        (tmp_path / "locked").chmod(0o700)


def test_counts_for_a_folder_the_model_has_left_are_dropped(qtbot, model, tmp_path):
    (tmp_path / "first" / "sub").mkdir(parents=True)
    (tmp_path / "first" / "sub" / "a").write_text("")
    (tmp_path / "second" / "sub").mkdir(parents=True)

    load(qtbot, model, tmp_path / "first")
    load(qtbot, model, tmp_path / "second")

    qtbot.waitUntil(lambda: size_text(model, "sub") != "", timeout=5000)
    assert size_text(model, "sub") == "0 items"


def test_size_sorts_folders_by_their_counts_and_keeps_them_on_top(qtbot, model, tmp_path):
    for name, count in (("few", 1), ("many", 5), ("none", 0)):
        (tmp_path / name).mkdir()
        for index in range(count):
            (tmp_path / name / str(index)).write_text("")
    (tmp_path / "file").write_text("x" * 100)
    proxy = DirectorySortProxy()
    proxy.setSourceModel(model)
    proxy.sort(SIZE, Qt.SortOrder.DescendingOrder)

    load(qtbot, model, tmp_path)

    # The counts come in after the list, and the order follows them
    qtbot.waitUntil(lambda: names(proxy) == ["many", "few", "none", "file"], timeout=5000)


def test_proxy_sorts_by_permissions(qtbot, model, tmp_path):
    for name, mode in (("open", 0o666), ("private", 0o600), ("shared", 0o644)):
        (tmp_path / name).write_text("")
        (tmp_path / name).chmod(mode)
    proxy = DirectorySortProxy()
    proxy.setSourceModel(model)
    proxy.sort(PERMISSIONS, Qt.SortOrder.AscendingOrder)

    load(qtbot, model, tmp_path)

    assert names(proxy) == ["private", "shared", "open"]
