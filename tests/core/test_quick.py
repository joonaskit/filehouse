import pytest
from helpers import gfile, make_tree, read_tree

from filehouse.core.errors import OperationError
from filehouse.core.operations import quick


def test_create_folder(tmp_path):
    made = quick.create_folder(gfile(tmp_path), "  Photos ")

    assert made.get_path() == str(tmp_path / "Photos")
    assert read_tree(tmp_path) == {"Photos": {}}


def test_create_folder_never_replaces(tmp_path):
    make_tree(tmp_path, {"Photos": {"a.jpg": "a"}, "notes": "n"})

    for name in ("Photos", "notes"):
        with pytest.raises(OperationError) as raised:
            quick.create_folder(gfile(tmp_path), name)
        assert str(raised.value) == f"{name}: already exists"
        assert raised.value.step == "create folder"
    assert read_tree(tmp_path) == {"Photos": {"a.jpg": "a"}, "notes": "n"}


@pytest.mark.parametrize("name", ["", "   ", "a/b", ".", ".."])
def test_names_that_cannot_be_used(tmp_path, name):
    make_tree(tmp_path, {"file": "f"})

    with pytest.raises(OperationError):
        quick.create_folder(gfile(tmp_path), name)
    with pytest.raises(OperationError):
        quick.rename(gfile(tmp_path / "file"), name)
    assert read_tree(tmp_path) == {"file": "f"}


def test_rename(tmp_path):
    make_tree(tmp_path, {"old.txt": "x", "dir": {"f": "f"}})

    renamed = quick.rename(gfile(tmp_path / "old.txt"), "new.txt")
    quick.rename(gfile(tmp_path / "dir"), "folder")

    assert renamed.get_path() == str(tmp_path / "new.txt")
    assert read_tree(tmp_path) == {"new.txt": "x", "folder": {"f": "f"}}


def test_rename_never_replaces(tmp_path):
    make_tree(tmp_path, {"a.txt": "a", "b.txt": "b"})

    with pytest.raises(OperationError) as raised:
        quick.rename(gfile(tmp_path / "a.txt"), "b.txt")

    assert raised.value.path == str(tmp_path / "a.txt")
    assert raised.value.message == "already exists"
    assert read_tree(tmp_path) == {"a.txt": "a", "b.txt": "b"}


def test_rename_to_the_same_name_does_nothing(tmp_path):
    make_tree(tmp_path, {"a.txt": "a"})

    assert quick.rename(gfile(tmp_path / "a.txt"), "a.txt").get_path() == str(tmp_path / "a.txt")


def test_suggested_folder_name_is_free(tmp_path):
    assert quick.suggest_folder_name(gfile(tmp_path)) == "New folder"
    make_tree(tmp_path, {"New folder": {}, "New folder (2)": "a file"})
    assert quick.suggest_folder_name(gfile(tmp_path)) == "New folder (3)"
