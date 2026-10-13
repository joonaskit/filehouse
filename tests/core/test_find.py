import io
import json

from helpers import make_tree

from filehouse.core import find

TREE = {
    "report.txt": "top",
    "notes.txt": "top",
    "docs": {"Report 2024.pdf": "a", "old": {"report-draft.txt": "c"}},
    ".cache": {"report.bin": "e"},
    "reports": {},
}


def lines(text, **options):
    out = io.StringIO()
    status = find.run(text, out=out, **options)
    return status, [json.loads(line) for line in out.getvalue().splitlines()]


def test_it_prints_a_line_for_each_result_and_a_last_one(tmp_path):
    root = make_tree(tmp_path / "root", TREE)

    status, found = lines("report", root=str(root))

    assert status == 0
    assert found[-1] == {"done": True, "more": False}
    assert sorted(line["path"] for line in found[:-1]) == [
        str(root / "docs/Report 2024.pdf"),
        str(root / "docs/old/report-draft.txt"),
        str(root / "report.txt"),
        str(root / "reports"),
    ]


def test_what_is_in_the_root_itself_is_found_too(tmp_path):
    root = make_tree(tmp_path / "root", TREE)

    _, found = lines("notes", root=str(root))

    assert [line["path"] for line in found[:-1]] == [str(root / "notes.txt")]


def test_a_line_says_where_the_result_is_and_what_it_is(tmp_path):
    root = make_tree(tmp_path / "root", TREE)

    _, found = lines("draft", root=str(root))

    assert found[0] == {
        "path": str(root / "docs/old/report-draft.txt"),
        "name": "report-draft.txt",
        "folder": str(root / "docs/old"),
        "dir": False,
        "type": "text/plain",
    }
    _, found = lines("reports", root=str(root))
    assert found[0]["dir"] is True


def test_hidden_ones_only_when_asked(tmp_path):
    root = make_tree(tmp_path / "root", TREE)

    _, found = lines("report.bin", root=str(root))
    assert len(found) == 1

    _, found = lines("report.bin", root=str(root), show_hidden=True)
    assert found[0]["path"] == str(root / ".cache/report.bin")


def test_it_stops_at_the_limit_and_says_there_are_more(tmp_path):
    root = make_tree(tmp_path / "root", {f"file{n}.txt": "" for n in range(30)})

    _, found = lines("file", root=str(root), limit=5)

    assert len(found) == 6
    assert found[-1] == {"done": True, "more": True}


def test_no_words_find_nothing(tmp_path):
    root = make_tree(tmp_path / "root", TREE)

    assert lines("  ", root=str(root)) == (0, [{"done": True, "more": False}])


def test_a_root_that_is_no_folder_is_an_error(tmp_path):
    root = make_tree(tmp_path / "root", TREE)

    status, found = lines("report", root=str(root / "report.txt"))
    assert status == 1
    assert list(found[0]) == ["error"]

    status, found = lines("report", root=str(root / "nowhere"))
    assert status == 1 and "error" in found[0]


def test_without_a_root_it_searches_home(home_dir):
    make_tree(home_dir, {"docs": {"find-me-here.txt": ""}})

    _, found = lines("find-me-here")

    assert [line["path"] for line in found[:-1]] == [str(home_dir / "docs/find-me-here.txt")]


def test_the_asker_going_away_is_not_an_error(tmp_path):
    root = make_tree(tmp_path / "root", TREE)

    class Closed:
        def write(self, _text):
            raise BrokenPipeError

        def flush(self):
            pass

    assert find.run("report", root=str(root), out=Closed()) == 0
