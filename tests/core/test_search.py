import os

import pytest
from helpers import gfile, make_tree

from filehouse.core import locations, search

TREE = {
    "report.txt": "top",
    "notes.txt": "top",
    "docs": {
        "Report 2024.pdf": "a",
        "summary.txt": "b",
        "old": {"report-draft.txt": "c", ".report-hidden.txt": "d"},
    },
    ".cache": {"report.bin": "e"},
    "pictures": {"holiday.png": "f", "reports": {"chart.png": "g"}},
}


def find(root, text, **options):
    found = []
    truncated = search.search_below(gfile(root), text, found.extend, **options)
    return sorted(info.name for info in found), truncated


@pytest.mark.parametrize(
    "text, name, expected",
    [
        ("rep", "Report.pdf", True),
        ("REPORT", "report.pdf", True),
        ("2024 rep", "Report 2024.pdf", True),
        ("rep 2025", "Report 2024.pdf", False),
        ("straße", "STRASSE.txt", True),
        ("", "anything", True),
    ],
)
def test_a_name_matches_when_it_has_every_word(text, name, expected):
    assert search.matches(search.words(text), name) is expected


def test_it_finds_what_is_below_and_not_what_is_in_the_folder_itself(tmp_path):
    root = make_tree(tmp_path / "root", TREE)

    names, truncated = find(root, "report")

    # "report.txt" in the folder itself is the list's to filter
    assert names == ["docs/Report 2024.pdf", "docs/old/report-draft.txt", "pictures/reports"]
    assert not truncated


def test_a_result_is_an_entry_of_the_folder_that_was_searched(tmp_path):
    root = make_tree(tmp_path / "root", TREE)
    found = []

    search.search_below(gfile(root), "draft", found.extend)

    (info,) = found
    assert info.name == "docs/old/report-draft.txt"
    assert info.display_name == "report-draft.txt"
    assert info.folder == "docs/old"
    assert not info.is_dir and info.size == 1
    location = locations.entry_location(gfile(root), info)
    assert location.get_path() == str(root / "docs/old/report-draft.txt")


def test_hidden_files_and_folders_are_searched_only_when_they_are_shown(tmp_path):
    root = make_tree(tmp_path / "root", TREE)

    names, _ = find(root, "report", show_hidden=True)

    assert ".cache/report.bin" in names and "docs/old/.report-hidden.txt" in names
    assert ".cache/report.bin" not in find(root, "report")[0]


def test_a_link_to_a_folder_is_found_and_not_followed(tmp_path):
    root = make_tree(tmp_path / "root", {"sub": {"deep": {"target.txt": "x"}}})
    (root / "sub" / "loop-target").symlink_to(root)
    make_tree(tmp_path / "elsewhere", {"target-outside.txt": "y"})
    (root / "sub" / "way-target").symlink_to(tmp_path / "elsewhere")

    names, _ = find(root, "target")

    assert names == ["sub/deep/target.txt", "sub/loop-target", "sub/way-target"]


def test_no_words_find_nothing(tmp_path):
    root = make_tree(tmp_path / "root", TREE)

    assert find(root, "   ") == ([], False)


def test_it_stops_at_the_limit_and_says_so(tmp_path):
    root = make_tree(tmp_path / "root", {"sub": {f"file{n}.txt": "" for n in range(30)}})

    names, truncated = find(root, "file", limit=10)

    assert len(names) == 10 and truncated


def test_what_is_near_comes_first(tmp_path):
    root = make_tree(
        tmp_path / "root", {"a": {"b": {"c": {"hit-deep.txt": ""}}}, "z": {"hit-near.txt": ""}}
    )
    found = []

    search.search_below(gfile(root), "hit", found.extend)

    assert [info.name for info in found] == ["z/hit-near.txt", "a/b/c/hit-deep.txt"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read everything")
def test_a_folder_that_cannot_be_read_is_passed_over(tmp_path):
    root = make_tree(tmp_path / "root", {"locked": {"hit.txt": ""}, "open": {"hit.txt": ""}})
    (root / "locked").chmod(0)
    try:
        assert find(root, "hit")[0] == ["open/hit.txt"]
    finally:
        (root / "locked").chmod(0o755)


def test_a_cancelled_search_stops_and_hands_over_nothing_more(tmp_path):
    from gi.repository import Gio

    root = make_tree(tmp_path / "root", {f"d{n}": {"hit.txt": ""} for n in range(20)})
    cancellable = Gio.Cancellable()
    found = []

    def take(batch):
        found.extend(batch)
        cancellable.cancel()

    truncated = search.search_below(gfile(root), "hit", take, cancellable, batch_seconds=0)

    assert not truncated
    assert len(found) < 20


def test_the_trash_and_the_recent_files_are_not_searched_below(tmp_path):
    assert search.can_search_below(gfile(tmp_path))
    assert not search.can_search_below(locations.trash())
    assert not search.can_search_below(locations.recent())
