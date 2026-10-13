"""Undo: what a job keeps of what it did, and taking it back by the same safety rules.

The trash is a plain folder standing in for the real one, which is a GVFS location
these tests must not touch: what went into it, from where and when, is written down
by the stand-in as the real trash does.
"""

import os
import time
from datetime import datetime

import pytest
from helpers import gfile, leftovers, make_tree, read_tree, run_job

from filehouse.core.operations import Answer, Conflict, Job, JobKind, JobState, job, trash, undo
from filehouse.core.operations.undo import History, Kind, Record


def take_back(record, prompt=None, hook=None):
    """Run the job that takes `record` back, to its end on this thread."""
    states = []

    def seen(changed):
        states.append(changed.state)
        if hook is not None:
            hook(changed)

    back = Job.undo_of(record, prompt=prompt, on_change=seen)
    back.run()
    assert back.state.is_final and states[-1] is back.state
    return back


def paths(record):
    return [(now.get_path(), was.get_path()) for now, was in record.pairs]


@pytest.fixture
def tree(tmp_path):
    make_tree(
        tmp_path / "src",
        {"a.txt": "a", "b.txt": "b", "album": {"1.jpg": "1", "in": {"2.jpg": "2"}}},
    )
    (tmp_path / "dst").mkdir()
    return tmp_path


# A move


def test_a_move_is_taken_back_to_where_everything_was(tree):
    src, dst = tree / "src", tree / "dst"
    before = read_tree(src)
    moved = run_job(JobKind.MOVE, [src / "a.txt", src / "album"], dst)
    assert moved.state is JobState.DONE

    back = take_back(moved.undo)

    assert back.state is JobState.DONE
    assert back.summary == "Moved 2 items"
    assert read_tree(src) == before
    assert read_tree(dst) == {}
    assert leftovers(tree) == []


def test_a_folder_that_went_over_whole_is_one_thing_to_take_back(tree):
    src, dst = tree / "src", tree / "dst"

    moved = run_job(JobKind.MOVE, [src / "album", src / "b.txt"], dst)

    assert moved.undo.kind is Kind.MOVE
    assert paths(moved.undo) == [
        (str(dst / "album"), str(src / "album")),
        (str(dst / "b.txt"), str(src / "b.txt")),
    ]


def test_a_move_to_another_file_system_is_taken_back(tree, other_device):
    src = tree / "src"
    before = read_tree(src)
    moved = run_job(JobKind.MOVE, [src / "a.txt", src / "album"], other_device)
    assert moved.state is JobState.DONE
    assert read_tree(src) == {"b.txt": "b"}

    back = take_back(moved.undo)

    assert back.state is JobState.DONE
    assert read_tree(src) == before
    assert read_tree(other_device) == {}
    assert leftovers(tree, other_device) == []


def test_the_job_that_takes_back_is_not_itself_kept(tree):
    src, dst = tree / "src", tree / "dst"
    moved = run_job(JobKind.MOVE, [src / "a.txt"], dst)

    back = take_back(moved.undo)

    # There is no redo
    assert back.is_undo and back.undo is None


def test_a_copy_and_a_delete_keep_nothing_to_take_back(tree):
    src, dst = tree / "src", tree / "dst"

    copied = run_job(JobKind.COPY, [src / "a.txt"], dst)
    deleted = run_job(JobKind.DELETE, [src / "b.txt"], prompt=lambda _question: Answer.DELETE)

    assert copied.state is JobState.DONE and copied.undo is None
    assert deleted.state is JobState.DONE and deleted.undo is None


def test_what_was_renamed_by_keep_both_goes_back_under_its_old_name(tree):
    src, dst = tree / "src", tree / "dst"
    (dst / "a.txt").write_text("another")
    moved = run_job(JobKind.MOVE, [src / "a.txt"], dst, prompt=lambda _q: Answer.KEEP_BOTH)
    assert read_tree(dst) == {"a.txt": "another", "a (2).txt": "a"}

    take_back(moved.undo)

    assert (src / "a.txt").read_text() == "a"
    assert read_tree(dst) == {"a.txt": "another"}


# The safety rules, for the taking back


def test_taking_back_never_replaces_what_now_has_the_old_place(tree):
    src, dst = tree / "src", tree / "dst"
    moved = run_job(JobKind.MOVE, [src / "a.txt", src / "b.txt"], dst)
    (src / "a.txt").write_text("made since")

    # With nobody to ask, as with a prompt that is gone
    back = take_back(moved.undo)

    assert back.state is JobState.FAILED
    assert [str(error) for error in back.errors] == ["a.txt: already exists at the destination"]
    assert (src / "a.txt").read_text() == "made since"
    # It is still where the move left it, and the other one went back
    assert (dst / "a.txt").read_text() == "a"
    assert (src / "b.txt").read_text() == "b"
    assert back.summary == "Moved 1 of 2 files, 1 skipped"


def test_taking_back_asks_as_any_move_does_when_something_is_in_the_way(tree):
    src, dst = tree / "src", tree / "dst"
    moved = run_job(JobKind.MOVE, [src / "a.txt"], dst)
    (src / "a.txt").write_text("made since")
    asked = []

    def prompt(question):
        asked.append(question)
        return Answer.KEEP_BOTH

    back = take_back(moved.undo, prompt)

    assert isinstance(asked[0], Conflict)
    assert back.state is JobState.DONE
    assert read_tree(src)["a.txt"] == "made since"
    assert read_tree(src)["a (2).txt"] == "a"


def test_what_is_no_longer_where_the_move_left_it_is_named(tree):
    src, dst = tree / "src", tree / "dst"
    moved = run_job(JobKind.MOVE, [src / "a.txt", src / "b.txt"], dst)
    (dst / "a.txt").unlink()

    back = take_back(moved.undo)

    assert back.state is JobState.FAILED
    assert len(back.errors) == 1 and back.errors[0].name == "a.txt"
    assert (src / "b.txt").read_text() == "b"
    assert not (src / "a.txt").exists()


def test_a_cancelled_taking_back_says_how_far_it_got_and_loses_nothing(tree, other_device):
    src = tree / "src"
    moved = run_job(JobKind.MOVE, [src / "a.txt", src / "b.txt", src / "album"], other_device)

    back = take_back(
        moved.undo, hook=lambda changed: changed.progress.files_done == 1 and changed.cancel()
    )

    assert back.state is JobState.CANCELLED
    assert back.summary.startswith("Cancelled after 1 of ")
    # Every file is in one of the two places, whole
    both = {**flat(src), **flat(other_device)}
    assert both == {"a.txt": "a", "b.txt": "b", "1.jpg": "1", "2.jpg": "2"}
    assert leftovers(tree, other_device) == []


def flat(root):
    return {
        name: open(os.path.join(folder, name)).read()
        for folder, _dirs, names in os.walk(root)
        for name in names
    }


# A move that only partly ran


def test_a_cancelled_move_keeps_only_what_it_finished(tree, other_device):
    src = tree / "src"
    names = ["a.txt", "b.txt"]

    moved = run_job(
        JobKind.MOVE,
        [src / name for name in names] + [src / "album"],
        other_device,
        hook=lambda changed: changed.progress.files_done == 1 and changed.cancel(),
    )

    assert moved.state is JobState.CANCELLED
    assert paths(moved.undo) == [(str(other_device / "a.txt"), str(src / "a.txt"))]

    take_back(moved.undo)
    assert read_tree(src) == {
        "a.txt": "a",
        "b.txt": "b",
        "album": {"1.jpg": "1", "in": {"2.jpg": "2"}},
    }


def test_a_folder_that_only_partly_went_gives_back_the_files_that_did(tree, other_device):
    src = tree / "src"
    make_tree(other_device / "album", {"1.jpg": "there already"})

    def prompt(question):
        # The folder merges with the one that is there; the file in the way stays
        return Answer.REPLACE if question.source_is_dir else Answer.SKIP

    moved = run_job(JobKind.MOVE, [src / "album"], other_device, prompt=prompt)

    assert read_tree(other_device) == {"album": {"1.jpg": "there already", "in": {"2.jpg": "2"}}}
    # The folder inside went whole; the one merged into did not
    assert paths(moved.undo) == [(str(other_device / "album" / "in"), str(src / "album" / "in"))]

    take_back(moved.undo)
    assert read_tree(src)["album"] == {"1.jpg": "1", "in": {"2.jpg": "2"}}
    assert read_tree(other_device) == {"album": {"1.jpg": "there already"}}


def test_what_replaced_something_cannot_be_taken_back(tree):
    src, dst = tree / "src", tree / "dst"
    (dst / "a.txt").write_text("replaced by the move")

    moved = run_job(
        JobKind.MOVE, [src / "a.txt", src / "b.txt"], dst, prompt=lambda _q: Answer.REPLACE
    )

    assert moved.state is JobState.DONE
    assert (dst / "a.txt").read_text() == "a"
    # What it replaced is gone: only the other one is kept
    assert paths(moved.undo) == [(str(dst / "b.txt"), str(src / "b.txt"))]


def test_a_move_where_everything_replaced_something_keeps_nothing(tree):
    src, dst = tree / "src", tree / "dst"
    (dst / "a.txt").write_text("replaced by the move")

    moved = run_job(JobKind.MOVE, [src / "a.txt"], dst, prompt=lambda _q: Answer.REPLACE)

    assert moved.undo is None


def test_the_folder_an_item_was_in_is_made_again_when_it_went_with_the_move(tree):
    src, dst = tree / "src", tree / "dst"
    make_tree(dst / "album", {"1.jpg": "there already"})
    # Merged into the folder that is there, replacing the one file in the way: the
    # emptied folder is removed from where it was
    moved = run_job(JobKind.MOVE, [src / "album"], dst, prompt=lambda _q: Answer.REPLACE)
    assert not (src / "album").exists()
    assert paths(moved.undo) == [(str(dst / "album" / "in"), str(src / "album" / "in"))]

    back = take_back(moved.undo)

    assert back.state is JobState.DONE
    assert read_tree(src / "album") == {"in": {"2.jpg": "2"}}


def test_a_skipped_item_is_not_among_what_is_taken_back(tree):
    src, dst = tree / "src", tree / "dst"
    (dst / "a.txt").write_text("in the way")

    moved = run_job(
        JobKind.MOVE, [src / "a.txt", src / "b.txt"], dst, prompt=lambda _q: Answer.SKIP
    )

    assert paths(moved.undo) == [(str(dst / "b.txt"), str(src / "b.txt"))]


def test_a_retry_of_a_taking_back_is_one_too(tree):
    src, dst = tree / "src", tree / "dst"
    moved = run_job(JobKind.MOVE, [src / "a.txt"], dst)
    (src / "a.txt").write_text("made since")
    back = take_back(moved.undo)
    assert back.can_retry
    (src / "a.txt").unlink()

    again = back.retry()
    again.run()

    assert again.state is JobState.DONE
    assert again.is_undo and again.undo is None
    assert (src / "a.txt").read_text() == "a"


# The trash


@pytest.fixture
def kept():
    """What the stand-in trash wrote down: (its name there, where it was, when it came)."""
    return []


@pytest.fixture
def bin_(tmp_path, monkeypatch, kept):
    """A folder that stands in for the trash, and keeps what the trash keeps: where
    each item was and when it came."""
    folder = tmp_path / "bin"
    folder.mkdir()

    def put(location, _cancellable):
        name = f"{len(kept)}-{location.get_basename()}"
        os.rename(location.get_path(), folder / name)
        kept.append((name, location.get_path(), datetime.now().replace(microsecond=0)))

    def listed(_cancellable):
        return [
            (gfile(folder / name), path, when)
            for name, path, when in kept
            if (folder / name).exists()
        ]

    monkeypatch.setattr(trash, "_gio_trash", put)
    monkeypatch.setattr(trash, "_list", listed)
    monkeypatch.setattr(job, "_TRASH_SETTLES_SECONDS", 0)
    return folder


def test_a_trash_is_taken_back_by_restoring_what_went_there(tree, bin_):
    src = tree / "src"
    before = read_tree(src)
    trashed = run_job(JobKind.TRASH, [src / "a.txt", src / "album"])
    assert trashed.state is JobState.DONE
    assert trashed.undo.kind is Kind.TRASH
    assert [was.get_path() for _now, was in trashed.undo.pairs] == [
        str(src / "a.txt"),
        str(src / "album"),
    ]

    back = take_back(trashed.undo)

    assert back.state is JobState.DONE
    assert back.summary == "Restored 2 items"
    assert read_tree(src) == before
    assert read_tree(bin_) == {}


def test_a_cancelled_trash_keeps_what_it_trashed(tree, bin_):
    src = tree / "src"

    trashed = run_job(
        JobKind.TRASH,
        [src / "a.txt", src / "b.txt"],
        hook=lambda changed: changed.progress.files_done == 1 and changed.cancel(),
    )

    assert trashed.state is JobState.CANCELLED
    assert [was.get_path() for _now, was in trashed.undo.pairs] == [str(src / "a.txt")]


def test_what_was_deleted_for_lack_of_a_trash_cannot_be_taken_back(tree, monkeypatch):
    from helpers import not_supported

    src = tree / "src"
    monkeypatch.setattr(trash, "_gio_trash", not_supported)

    gone = run_job(JobKind.TRASH, [src / "a.txt"], prompt=lambda _q: Answer.DELETE)

    assert gone.state is JobState.DONE and not (src / "a.txt").exists()
    assert gone.undo is None


def test_restoring_never_replaces_what_now_has_the_old_place(tree, bin_):
    src = tree / "src"
    trashed = run_job(JobKind.TRASH, [src / "a.txt"])
    (src / "a.txt").write_text("made since")

    back = take_back(trashed.undo)

    assert back.state is JobState.FAILED
    assert str(back.error) == "0-a.txt: already exists at the destination"
    assert (src / "a.txt").read_text() == "made since"
    assert read_tree(bin_) == {"0-a.txt": "a"}


def test_what_is_no_longer_in_the_trash_is_named_and_the_rest_restored(tree, bin_):
    src = tree / "src"
    trashed = run_job(JobKind.TRASH, [src / "a.txt", src / "b.txt"])
    (bin_ / "0-a.txt").unlink()

    back = take_back(trashed.undo)

    assert back.state is JobState.FAILED
    assert [str(error) for error in back.errors] == ["a.txt: is no longer in the trash"]
    assert (src / "b.txt").read_text() == "b"
    assert back.summary == "Restored 1 of 1 item, 1 failed"


def test_with_nothing_left_in_the_trash_it_says_so_by_name(tree, bin_):
    src = tree / "src"
    trashed = run_job(JobKind.TRASH, [src / "a.txt", src / "b.txt"])
    for name in os.listdir(bin_):
        (bin_ / name).unlink()

    back = take_back(trashed.undo)

    assert back.state is JobState.FAILED
    assert back.summary == "a.txt: is no longer in the trash"
    assert [error.name for error in back.errors] == ["a.txt", "b.txt"]


def test_an_item_is_found_by_where_it_was_and_when_it_went(tmp_path, bin_, kept):
    place = tmp_path / "notes.txt"
    place.write_text("old")
    old = run_job(JobKind.TRASH, [place])
    # Trashed long before: another item that was at the same place
    kept[0] = (kept[0][0], kept[0][1], datetime(2020, 1, 1))
    place.write_text("new")
    new = run_job(JobKind.TRASH, [place])

    found = trash.find([gfile(place)], new.undo.since, None)

    assert [item.get_basename() for item in found.values()] == ["1-notes.txt"]
    assert old.undo.since <= new.undo.since
    # Nothing that went there before the action is taken for it
    assert trash.find([gfile(place)], time.time() + 60, None) == {}


# Rename, and what was made


def renamed(*pairs):
    return Record(Kind.RENAME, tuple((gfile(now), gfile(was)) for now, was in pairs))


def test_a_rename_is_renamed_back(tmp_path):
    make_tree(tmp_path, {"new.txt": "x", "other": {}})

    back, errors = undo.rename_back(renamed((tmp_path / "new.txt", tmp_path / "old.txt")))

    assert errors == []
    assert [location.get_path() for location in back] == [str(tmp_path / "old.txt")]
    assert read_tree(tmp_path) == {"old.txt": "x", "other": {}}


def test_renaming_back_never_replaces_what_has_the_old_name_now(tmp_path):
    make_tree(tmp_path, {"new.txt": "renamed", "old.txt": "made since"})

    back, errors = undo.rename_back(renamed((tmp_path / "new.txt", tmp_path / "old.txt")))

    assert back == []
    assert [str(error) for error in errors] == ["new.txt: already exists"]
    assert read_tree(tmp_path) == {"new.txt": "renamed", "old.txt": "made since"}


def test_a_renamed_file_that_is_gone_is_named_and_the_others_get_their_names(tmp_path):
    make_tree(tmp_path, {"b2": "b", "c2": "c"})
    record = renamed(
        (tmp_path / "a2", tmp_path / "a"),
        (tmp_path / "b2", tmp_path / "b"),
        (tmp_path / "c2", tmp_path / "c"),
    )

    back, errors = undo.rename_back(record)

    assert [str(error) for error in errors] == ["a2: is no longer there"]
    assert sorted(location.get_basename() for location in back) == ["b", "c"]
    assert read_tree(tmp_path) == {"b": "b", "c": "c"}


def test_several_that_took_each_others_names_go_back_in_the_right_order(tmp_path):
    # "1" was renamed to "2" after "2" had become "3"
    make_tree(tmp_path, {"2": "was 1", "3": "was 2"})

    back, errors = undo.rename_back(
        renamed((tmp_path / "3", tmp_path / "2"), (tmp_path / "2", tmp_path / "1"))
    )

    assert errors == []
    assert read_tree(tmp_path) == {"1": "was 1", "2": "was 2"}


def made(*locations):
    return Record(Kind.CREATE, tuple((gfile(location), None) for location in locations))


def test_a_new_folder_and_a_new_file_are_removed_while_they_are_empty(tmp_path):
    make_tree(tmp_path, {"New folder": {}, "New file": "", "kept": "x"})

    removed, errors = undo.remove_created(made(tmp_path / "New folder", tmp_path / "New file"))

    assert errors == []
    assert len(removed) == 2
    assert read_tree(tmp_path) == {"kept": "x"}


def test_what_was_made_and_then_used_stays(tmp_path):
    make_tree(tmp_path, {"New folder": {"put here since": "x"}, "New file": "written since"})

    removed, errors = undo.remove_created(made(tmp_path / "New folder", tmp_path / "New file"))

    assert removed == []
    assert [str(error) for error in errors] == [
        "New folder: is not empty any more",
        "New file: is not empty any more",
    ]
    assert read_tree(tmp_path) == {
        "New folder": {"put here since": "x"},
        "New file": "written since",
    }


def test_what_was_made_and_is_gone_or_something_else_now_is_left_alone(tmp_path):
    make_tree(tmp_path, {"target": "x", "New file": ("link", "target")})

    removed, errors = undo.remove_created(made(tmp_path / "New folder", tmp_path / "New file"))

    assert removed == []
    assert [str(error) for error in errors] == [
        "New folder: is no longer there",
        "New file: is not what was made",
    ]
    assert read_tree(tmp_path) == {"target": "x", "New file": ("link", "target")}


# What is kept


def test_the_last_action_is_taken_back_first(tmp_path):
    history = History()
    first, second = made(tmp_path / "1"), made(tmp_path / "2")
    history.push(first)
    history.push(second)

    assert history.peek() is second
    assert history.pop() is second and history.pop() is first
    assert history.pop() is None and history.peek() is None


def test_nothing_and_an_action_that_finished_nothing_are_not_kept(tmp_path):
    history = History()

    history.push(None)
    history.push(Record(Kind.MOVE, ()))

    assert len(history) == 0


def test_only_the_last_few_actions_are_kept(tmp_path):
    history = History(limit=3)
    for number in range(5):
        history.push(made(tmp_path / str(number)))

    assert len(history) == 3
    assert history.pop().pairs[0][0].get_basename() == "4"


def test_an_action_is_said_in_a_few_words(tmp_path):
    a, b = gfile(tmp_path / "a.txt"), gfile(tmp_path / "b.txt")

    assert Record(Kind.MOVE, ((a, b),)).describe() == "move of “b.txt”"
    assert Record(Kind.MOVE, ((a, b), (b, a))).describe() == "move of 2 items"
    assert Record(Kind.TRASH, ((None, b),)).describe() == "trash of “b.txt”"
    assert Record(Kind.RENAME, ((a, b),)).describe() == "rename of “a.txt”"
    assert Record(Kind.CREATE, ((a, None),)).describe() == "new “a.txt”"
