import pytest
from gi.repository import Gio
from PySide6.QtCore import Qt

from filehouse.qt_models.directory_model import (
    MODIFIED,
    NAME,
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
    (tmp_path / "sub").mkdir()

    load(qtbot, model, tmp_path)

    assert sorted(names(model)) == ["a.txt", "sub"]
    assert not model.loading
    row = model.row_for_name("a.txt")
    assert model.index(row, SIZE).data() != ""
    assert model.index(row, MODIFIED).data() != ""
    assert model.index(model.row_for_name("sub"), SIZE).data() == ""


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
