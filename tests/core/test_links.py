"""Create link: a symbolic link to a file, beside it or in another folder."""

import os

from gi.repository import Gio
from helpers import gfile, make_tree, read_tree

from filehouse.core.operations import quick


def test_beside_its_target_a_link_is_relative_and_says_what_it_is(tmp_path):
    make_tree(tmp_path, {"report.pdf": "pages"})

    link = quick.create_link(gfile(tmp_path / "report.pdf"), gfile(tmp_path))

    assert link.get_basename() == "Link to report.pdf"
    assert os.readlink(tmp_path / "Link to report.pdf") == "report.pdf"
    assert (tmp_path / "Link to report.pdf").read_text() == "pages"


def test_in_another_folder_it_has_the_name_and_the_full_path(tmp_path):
    make_tree(tmp_path, {"src": {"report.pdf": "pages", "album": {}}, "dst": {}})

    for name in ("report.pdf", "album"):
        quick.create_link(gfile(tmp_path / "src" / name), gfile(tmp_path / "dst"))

    assert read_tree(tmp_path / "dst") == {
        "report.pdf": ("link", str(tmp_path / "src" / "report.pdf")),
        "album": ("link", str(tmp_path / "src" / "album")),
    }


def test_a_link_never_replaces_what_is_there(tmp_path):
    make_tree(tmp_path, {"src": {"a.txt": "new", "dir": {}}, "dst": {"a.txt": "mine", "dir": {}}})
    dst = gfile(tmp_path / "dst")

    names = [
        quick.create_link(gfile(tmp_path / "src" / "a.txt"), dst).get_basename() for _ in range(3)
    ]
    folders = [
        quick.create_link(gfile(tmp_path / "src" / "dir"), dst).get_basename() for _ in range(2)
    ]

    assert names == ["Link to a.txt", "Link to a (2).txt", "Link to a (3).txt"]
    # A folder has no extension to put the number before
    assert folders == ["Link to dir", "Link to dir (2)"]
    assert (tmp_path / "dst" / "a.txt").read_text() == "mine"
    assert not (tmp_path / "dst" / "dir").is_symlink()


def test_a_link_to_a_link_points_at_the_link(tmp_path):
    make_tree(tmp_path, {"file": "f", "first": ("link", "file")})

    quick.create_link(gfile(tmp_path / "first"), gfile(tmp_path))

    assert os.readlink(tmp_path / "Link to first") == "first"
    assert read_tree(tmp_path)["file"] == "f"


def test_several_links_carry_on_after_one_that_cannot_be_made(tmp_path):
    make_tree(tmp_path, {"src": {"a.txt": "a", "b.txt": "b"}})
    in_trash = Gio.File.new_for_uri("trash:///gone.txt")
    missing_folder = gfile(tmp_path / "nowhere")

    made, errors = quick.create_links(
        [gfile(tmp_path / "src" / "a.txt"), in_trash, gfile(tmp_path / "src" / "b.txt")],
        gfile(tmp_path / "src"),
    )
    nothing, failed = quick.create_links([gfile(tmp_path / "src" / "a.txt")], missing_folder)

    assert [link.get_basename() for link in made] == ["Link to a.txt", "Link to b.txt"]
    assert [str(error) for error in errors] == ["gone.txt: a link can only point to this computer"]
    assert errors[0].step == "link"
    assert not nothing
    assert [str(error) for error in failed] == ["a.txt: no such file or folder"]
