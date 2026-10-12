"""Renaming several files as one operation: all of them, or none, and nothing replaced."""

import os

import pytest
from helpers import gfile, make_tree, read_tree

from filehouse.core.errors import OperationError
from filehouse.core.operations import quick, rename_many
from filehouse.core.operations.rename_many import Case, Number, Replace

FILES = [("IMG_1.jpg", False), ("IMG_2.jpg", False), ("IMG_notes", True)]


def test_find_and_replace_leaves_the_extension():
    names = rename_many.new_names([("a.jpg.jpg", False), ("jpg", True)], Replace("jpg", "png"))

    assert names == ["a.png.jpg", "png"]


def test_find_and_replace_with_the_extension_when_asked():
    rule = Replace(".jpeg", ".jpg")

    assert rename_many.new_names([("a.jpeg", False)], rule) == ["a.jpeg"]
    assert rename_many.new_names([("a.jpeg", False)], rule, with_extension=True) == ["a.jpg"]


def test_nothing_to_find_changes_nothing():
    assert rename_many.new_names(FILES, Replace("", "x")) == [name for name, _ in FILES]


@pytest.mark.parametrize(
    "pattern, start, expected",
    [
        ("Holiday #", 1, ["Holiday 1.jpg", "Holiday 2.jpg", "Holiday 3"]),
        ("Holiday ##", 9, ["Holiday 09.jpg", "Holiday 10.jpg", "Holiday 11"]),
        ("#-trip-#", 1, ["1-trip-1.jpg", "2-trip-2.jpg", "3-trip-3"]),
        ("Holiday", 1, ["Holiday 1.jpg", "Holiday 2.jpg", "Holiday 3"]),
        ("", 0, ["0.jpg", "1.jpg", "2"]),
    ],
)
def test_numbering_in_the_order_of_the_list(pattern, start, expected):
    assert rename_many.new_names(FILES, Number(pattern, start)) == expected


def test_case():
    items = [("Read Me.TXT", False), ("Folder.d", True)]

    assert rename_many.new_names(items, Case(upper=True)) == ["READ ME.TXT", "FOLDER.D"]
    assert rename_many.new_names(items, Case(upper=False)) == ["read me.TXT", "folder.d"]
    assert rename_many.new_names(items, Case(upper=False), with_extension=True)[0] == "read me.txt"


def pairs(folder, names):
    return [(gfile(folder / old), new) for old, new in names.items()]


def problems(renames):
    return {rename.location.get_basename(): rename.problem for rename in renames}


def test_a_name_that_is_taken_blocks(tmp_path):
    make_tree(tmp_path, {"a.txt": "a", "b.txt": "b", "taken.txt": "mine"})

    checked = rename_many.check(pairs(tmp_path, {"a.txt": "taken.txt", "b.txt": "free.txt"}))

    assert problems(checked) == {"a.txt": "already exists", "b.txt": ""}
    with pytest.raises(OperationError) as raised:
        rename_many.rename_all(checked)
    assert str(raised.value) == "a.txt: already exists"
    # Not the one that could have been either: all of it, or none
    assert read_tree(tmp_path) == {"a.txt": "a", "b.txt": "b", "taken.txt": "mine"}


def test_two_that_would_get_the_same_name_block(tmp_path):
    make_tree(tmp_path, {"a.txt": "a", "b.txt": "b", "c.txt": "c"})

    checked = rename_many.check(
        pairs(tmp_path, {"a.txt": "same.txt", "b.txt": "same.txt", "c.txt": "other.txt"})
    )

    assert problems(checked) == {
        "a.txt": "two of these would get this name",
        "b.txt": "two of these would get this name",
        "c.txt": "",
    }


def test_a_name_one_of_them_keeps_is_taken(tmp_path):
    make_tree(tmp_path, {"a.txt": "a", "b.txt": "b"})

    checked = rename_many.check(pairs(tmp_path, {"a.txt": "b.txt", "b.txt": "b.txt"}))

    assert problems(checked) == {"a.txt": "already exists", "b.txt": ""}


@pytest.mark.parametrize("name", ["", "  ", "a/b", ".", ".."])
def test_names_that_cannot_be_used_block(tmp_path, name):
    make_tree(tmp_path, {"a.txt": "a"})

    checked = rename_many.check(pairs(tmp_path, {"a.txt": name}))

    assert checked[0].problem
    with pytest.raises(OperationError):
        rename_many.rename_all(checked)
    assert read_tree(tmp_path) == {"a.txt": "a"}


def test_a_name_that_one_of_them_leaves_is_free(tmp_path):
    make_tree(tmp_path, {"1.txt": "one", "2.txt": "two", "3.txt": "three"})
    # Each takes the name of the next, which has to move first
    wanted = pairs(tmp_path, {"1.txt": "2.txt", "2.txt": "3.txt", "3.txt": "4.txt"})

    checked = rename_many.check(wanted)
    now = rename_many.rename_all(checked)

    assert not any(rename.problem for rename in checked)
    assert read_tree(tmp_path) == {"2.txt": "one", "3.txt": "two", "4.txt": "three"}
    assert [location.get_basename() for location in now] == ["2.txt", "3.txt", "4.txt"]


def test_two_that_trade_names_block(tmp_path):
    make_tree(tmp_path, {"a.txt": "a", "b.txt": "b", "c.txt": "c"})

    checked = rename_many.check(
        pairs(tmp_path, {"a.txt": "b.txt", "b.txt": "a.txt", "c.txt": "d.txt"})
    )

    assert problems(checked) == {
        "a.txt": "trades names with another of these",
        "b.txt": "trades names with another of these",
        "c.txt": "",
    }
    with pytest.raises(OperationError):
        rename_many.rename_all(checked)
    assert read_tree(tmp_path) == {"a.txt": "a", "b.txt": "b", "c.txt": "c"}


def test_one_that_keeps_its_name_is_left_alone(tmp_path):
    make_tree(tmp_path, {"a.txt": "a", "b.txt": "b"})

    checked = rename_many.check(pairs(tmp_path, {"a.txt": "a.txt", "b.txt": "c.txt"}))
    now = rename_many.rename_all(checked)

    assert [rename.changes for rename in checked] == [False, True]
    assert [location.get_basename() for location in now] == ["a.txt", "c.txt"]
    assert read_tree(tmp_path) == {"a.txt": "a", "c.txt": "b"}


def test_files_of_several_folders_are_checked_where_they_are(tmp_path):
    make_tree(tmp_path, {"a.txt": "top", "sub": {"a.txt": "below", "b.txt": "mine"}})
    wanted = [(gfile(tmp_path / "a.txt"), "b.txt"), (gfile(tmp_path / "sub" / "a.txt"), "b.txt")]

    checked = rename_many.check(wanted)

    # The same name in two folders is two names; in `sub` it is taken
    assert [rename.problem for rename in checked] == ["", "already exists"]


def test_a_file_that_appears_meanwhile_is_not_replaced_and_the_rest_is_put_back(tmp_path):
    make_tree(tmp_path, {"a.txt": "a", "b.txt": "b", "c.txt": "c"})
    checked = rename_many.check(
        pairs(tmp_path, {"a.txt": "1.txt", "b.txt": "2.txt", "c.txt": "3.txt"})
    )
    assert not any(rename.problem for rename in checked)
    # Planning is advisory: somebody made this after the names were checked
    (tmp_path / "2.txt").write_text("somebody else's")

    with pytest.raises(OperationError) as raised:
        rename_many.rename_all(checked)

    assert str(raised.value) == "b.txt: already exists"
    assert raised.value.step == "rename"
    assert read_tree(tmp_path) == {
        "a.txt": "a",
        "b.txt": "b",
        "c.txt": "c",
        "2.txt": "somebody else's",
    }


def test_what_cannot_be_put_back_is_said(tmp_path, monkeypatch):
    make_tree(tmp_path, {"a.txt": "a", "b.txt": "b"})
    checked = rename_many.check(pairs(tmp_path, {"a.txt": "1.txt", "b.txt": "2.txt"}))
    real = quick.rename

    def rename(location, name):
        if name in ("2.txt", "a.txt"):
            # The second cannot be renamed, and then the first not back
            raise OperationError.at(location, "rename", "permission denied")
        return real(location, name)

    monkeypatch.setattr(quick, "rename", rename)

    with pytest.raises(OperationError) as raised:
        rename_many.rename_all(checked)

    assert str(raised.value) == (
        "b.txt: permission denied; 1 renamed before it could not get the old name back"
    )
    assert sorted(os.listdir(tmp_path)) == ["1.txt", "b.txt"]
