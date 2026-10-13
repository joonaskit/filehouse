"""The filters of a file dialog: which files pass a glob or a MIME type."""

import pytest
from helpers import gfile

from filehouse.core.filters import GLOB, MIME, Filter
from filehouse.core.listing import list_directory


def info(tmp_path, name, content="x"):
    (tmp_path / name).write_text(content)
    found = []
    list_directory(gfile(tmp_path), found.extend)
    return next(item for item in found if item.name == name)


@pytest.mark.parametrize(
    "pattern, name, expected",
    [
        ("*.txt", "notes.txt", True),
        ("*.txt", "notes.md", False),
        ("*.jpg", "PHOTO.JPG", True),
        ("*.[Jj][Pp][Gg]", "photo.jpg", True),
        ("report.*", "report.tar.gz", True),
        ("*", "anything", True),
    ],
)
def test_a_glob_is_for_the_name_whatever_its_case(tmp_path, pattern, name, expected):
    assert Filter("Some", ((GLOB, pattern),)).accepts(info(tmp_path, name)) is expected


@pytest.mark.parametrize(
    "wanted, name, expected",
    [
        ("text/plain", "notes.txt", True),
        ("image/png", "notes.txt", False),
        ("text/*", "notes.txt", True),
        ("image/*", "notes.txt", False),
        ("*/*", "notes.txt", True),
        # A script is text too
        ("text/plain", "run.py", True),
    ],
)
def test_a_type_passes_for_itself_its_family_and_what_it_is_a_kind_of(
    tmp_path, wanted, name, expected
):
    assert Filter("Some", ((MIME, wanted),)).accepts(info(tmp_path, name)) is expected


def test_any_one_pattern_is_enough_and_none_is_none(tmp_path):
    notes = info(tmp_path, "notes.txt")

    assert Filter("Both", ((GLOB, "*.png"), (MIME, "text/plain"))).accepts(notes)
    assert not Filter("Empty").accepts(notes)
    assert not Filter("Odd", ((7, "*"),)).accepts(notes)
