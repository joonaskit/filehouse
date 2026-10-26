"""Running a folder's rules: one job, by the safety rules of every other, taken back
as one action."""

import time

from helpers import gfile, make_tree, not_supported, read_tree

from filehouse.core import automations as auto
from filehouse.core.automations import Action, ActionKind, Condition, ConditionKind, Rule
from filehouse.core.operations import JobKind, JobState, trash, undo
from filehouse.core.operations.automation import RulesJob, RulesUndoJob


def rule(pattern, *actions, name="r", **options):
    return Rule(name, (Condition(ConditionKind.NAME, "glob", pattern),), tuple(actions), **options)


def move_to(path):
    return Action(ActionKind.MOVE, str(path))


def copy_to(path):
    return Action(ActionKind.COPY, str(path))


def rename(template):
    return Action(ActionKind.RENAME, template)


TRASH = Action(ActionKind.TRASH)


def run(folder, *rules, hook=None, between=None):
    """Plan and run `rules` on `folder` to the end, on this thread. The files the tests
    make are new, so the plan is made as if a while later: they have settled by then.
    `between` is called after the plan is made and before it runs."""
    plan = auto.plan_run(gfile(folder), list(rules), time.time() + 60)
    assert not plan.problems
    if between is not None:
        between()
    states = []

    def seen(job):
        states.append(job.state)
        if hook is not None:
            hook(job)

    job = RulesJob(gfile(folder), plan, on_change=seen)
    job.run()
    assert job.state.is_final and states[-1] is job.state
    return job


def take_back(record):
    back = RulesUndoJob(record)
    back.run()
    assert back.state.is_final
    return back


def test_a_run_moves_what_matches(tmp_path):
    make_tree(tmp_path, {"in": {"a.pdf": "a", "b.pdf": "b", "c.jpg": "c"}, "out": {}})

    job = run(tmp_path / "in", rule("*.pdf", move_to(tmp_path / "out")))

    assert job.kind is JobKind.RULES
    assert job.state is JobState.DONE
    assert job.summary == "Rules acted on 2 files"
    assert read_tree(tmp_path) == {"in": {"c.jpg": "c"}, "out": {"a.pdf": "a", "b.pdf": "b"}}


def test_a_taken_name_keeps_both_and_nothing_is_replaced(tmp_path):
    make_tree(
        tmp_path, {"in": {"a.pdf": "new", "IMG.jpg": "i", "x.jpg": "x"}, "out": {"a.pdf": "old"}}
    )

    job = run(
        tmp_path / "in",
        rule("*.pdf", move_to(tmp_path / "out"), name="move"),
        rule("IMG.jpg", rename("x.{ext}"), name="rename"),
    )

    assert job.state is JobState.DONE
    assert read_tree(tmp_path) == {
        "in": {"x.jpg": "x", "x (2).jpg": "i"},
        "out": {"a.pdf": "old", "a (2).pdf": "new"},
    }


def test_rename_then_move_moves_the_renamed_file(tmp_path):
    make_tree(tmp_path, {"in": {"IMG_1.jpg": "i"}})

    job = run(tmp_path / "in", rule("IMG_*", rename("photo.{ext}"), move_to(tmp_path / "Photos")))

    assert job.state is JobState.DONE
    assert read_tree(tmp_path) == {"in": {}, "Photos": {"photo.jpg": "i"}}


def test_a_missing_destination_is_made_and_taken_back(tmp_path):
    make_tree(tmp_path, {"in": {"a.pdf": "a"}})
    deep = tmp_path / "Documents" / "Invoices"

    job = run(tmp_path / "in", rule("*.pdf", rename("{name} paid.pdf"), move_to(deep)))
    assert read_tree(tmp_path) == {"in": {}, "Documents": {"Invoices": {"a paid.pdf": "a"}}}

    assert job.undo.kind is undo.Kind.RULES
    assert job.undo.describe() == "folder rules in “in”"
    back = take_back(job.undo)

    assert back.state is JobState.DONE, back.errors
    assert read_tree(tmp_path) == {"in": {"a.pdf": "a"}}


def test_a_run_is_one_action_for_undo(tmp_path):
    make_tree(tmp_path, {"in": {f"{n}.pdf": str(n) for n in range(30)}})
    history = undo.History()

    job = run(tmp_path / "in", rule("*.pdf", move_to(tmp_path / "out")))
    history.push(job.undo)

    assert len(history) == 1
    assert len(job.undo.parts) == 31  # the folder that was made, and each move


def test_copy_then_trash_and_undo(tmp_path, bin_):
    make_tree(tmp_path, {"in": {"a.pdf": "a"}})

    job = run(tmp_path / "in", rule("*.pdf", copy_to(tmp_path / "Backup"), TRASH))
    assert job.state is JobState.DONE
    assert read_tree(tmp_path / "in") == {}
    assert read_tree(tmp_path / "Backup") == {"a.pdf": "a"}

    back = take_back(job.undo)

    # The trash is taken back; a copy is not, and the folder it is in stays with it
    assert back.state is JobState.DONE, back.errors
    assert read_tree(tmp_path / "in") == {"a.pdf": "a"}
    assert read_tree(tmp_path / "Backup") == {"a.pdf": "a"}


def test_the_trash_never_falls_back_to_a_permanent_delete(tmp_path, monkeypatch):
    make_tree(tmp_path, {"in": {"a.pdf": "a"}})
    monkeypatch.setattr(trash, "_gio_trash", not_supported)

    job = run(tmp_path / "in", rule("*.pdf", TRASH))

    assert job.state is JobState.FAILED
    assert job.errors[0].path.endswith("a.pdf")
    assert read_tree(tmp_path / "in") == {"a.pdf": "a"}


def test_one_failed_file_does_not_stop_the_run(tmp_path):
    make_tree(tmp_path, {"in": {"a.pdf": "a", "b.jpg": "b"}})

    job = run(
        tmp_path / "in",
        rule("*.pdf", move_to(tmp_path / "blocked" / "sub"), name="bad"),
        rule("*.jpg", move_to(tmp_path / "Pictures"), name="good"),
        # Where the rule would make a folder, a file came in the way since the plan
        between=lambda: (tmp_path / "blocked").write_text("file"),
    )

    assert job.state is JobState.FAILED
    assert job.summary == "Rules acted on 1 of 2 files, 1 failed"
    assert len(job.errors) == 1 and job.errors[0].path
    assert job.errors[0].path.endswith("blocked/sub")
    assert read_tree(tmp_path / "in") == {"a.pdf": "a"}
    assert read_tree(tmp_path / "Pictures") == {"b.jpg": "b"}


def test_the_steps_after_a_failed_one_are_left_out(tmp_path, bin_):
    make_tree(tmp_path, {"in": {"a.pdf": "a"}})

    job = run(
        tmp_path / "in",
        rule("*.pdf", copy_to(tmp_path / "Backup"), TRASH),
        # The copy fails, so the file must not go to the trash
        between=lambda: (tmp_path / "Backup").write_text("file"),
    )

    assert job.state is JobState.FAILED
    assert job.progress.files_done == job.progress.files_total == 2
    assert read_tree(tmp_path / "in") == {"a.pdf": "a"}
    assert read_tree(bin_) == {}


def test_cancel_stops_between_files_and_says_how_far_it_got(tmp_path):
    make_tree(tmp_path, {"in": {"a.pdf": "a", "b.pdf": "b", "c.pdf": "c"}})

    job = run(
        tmp_path / "in",
        rule("*.pdf", move_to(tmp_path / "out")),
        hook=lambda job: job.progress.files_done == 1 and job.cancel(),
    )

    assert job.state is JobState.CANCELLED
    assert job.summary == "Cancelled after 1 of 3 files"
    assert read_tree(tmp_path / "out") == {"a.pdf": "a"}
    moved = [part for part in job.undo.parts if part.kind is undo.Kind.MOVE]
    assert len(moved) == 1


def test_undo_never_replaces_what_is_there_now(tmp_path):
    make_tree(tmp_path, {"in": {"a.pdf": "a"}, "out": {}})
    job = run(tmp_path / "in", rule("*.pdf", move_to(tmp_path / "out")))
    (tmp_path / "in" / "a.pdf").write_text("someone else's")

    back = take_back(job.undo)

    assert back.state is JobState.FAILED
    assert back.errors[0].path.endswith("a.pdf")
    assert read_tree(tmp_path) == {"in": {"a.pdf": "someone else's"}, "out": {"a.pdf": "a"}}


def test_a_run_with_nothing_to_do_is_done(tmp_path):
    make_tree(tmp_path, {"in": {"a.txt": "a"}})

    job = run(tmp_path / "in", rule("*.pdf", TRASH))

    assert job.state is JobState.DONE
    assert job.summary == "Rules acted on 0 files"
    assert job.undo is None


def test_a_run_and_its_undo_say_where_they_put_files(tmp_path):
    # So that their arrival there is not taken for a new file (#88)
    make_tree(tmp_path, {"in": {"a.pdf": "a", "b.txt": "b"}, "out": {"new a.pdf": "old"}})

    job = run(
        tmp_path / "in",
        rule("*.pdf", rename("new {name}.{ext}"), move_to(tmp_path / "out")),
        rule("*.txt", copy_to(tmp_path / "out")),
    )

    assert sorted(location.get_path() for location in job.placed) == sorted(
        str(path)
        for path in (
            tmp_path / "in" / "new a.pdf",
            tmp_path / "out" / "new a (2).pdf",
            tmp_path / "out" / "b.txt",
        )
    )
    back = take_back(job.undo)
    assert {location.get_path() for location in back.placed} == {
        str(tmp_path / "in" / "a.pdf"),
        str(tmp_path / "in" / "new a.pdf"),
    }
