import os
import time

import pytest
from helpers import gfile, make_tree

from filehouse.core import automations as auto
from filehouse.core.automations import (
    Action,
    ActionKind,
    Condition,
    ConditionKind,
    Facts,
    Match,
    Rule,
)

NOW = 1_800_000_000.0
DAY = auto.DAY_SECONDS


def name_is(pattern, mode="glob"):
    return Condition(ConditionKind.NAME, mode, pattern)


def move_to(folder):
    return Action(ActionKind.MOVE, str(folder))


def rule(*conditions, actions=(), **options):
    return Rule(options.pop("name", "r"), tuple(conditions), tuple(actions), **options)


def facts(name="report.pdf", size=100, age_days=5, content_type="application/pdf"):
    return Facts(name, size, NOW - age_days * DAY, content_type)


def folder_with(tmp_path, names, age_days=5):
    folder = tmp_path / "in"
    make_tree(folder, {name: "x" for name in names})
    for name in names:
        os.utime(folder / name, (NOW - age_days * DAY, NOW - age_days * DAY))
    return folder


def names(planned):
    return sorted(file.source.get_basename() for file in planned)


# Conditions


@pytest.mark.parametrize(
    ("condition", "expected"),
    [
        (name_is("*.PDF"), True),
        (name_is("invoice", "contains"), False),
        (name_is("REPORT", "contains"), True),
        (name_is(r"^rep.*pdf$", "regex"), True),
        (name_is("[", "regex"), False),
        (Condition(ConditionKind.TYPE, "is", ".pdf"), True),
        (Condition(ConditionKind.TYPE, "is", ".png, .pdf"), True),
        (Condition(ConditionKind.TYPE, "is", "image/*"), False),
        (Condition(ConditionKind.TYPE, "is", "application/*"), True),
        (Condition(ConditionKind.SIZE, "larger", 99), True),
        (Condition(ConditionKind.SIZE, "smaller", 100), False),
        (Condition(ConditionKind.AGE, "older", 4), True),
        (Condition(ConditionKind.AGE, "older", 6), False),
        (Condition(ConditionKind.AGE, "newer", 6), True),
    ],
)
def test_conditions(condition, expected):
    assert auto.matches(rule(condition), facts(), NOW) is expected


def test_all_and_any():
    both = (name_is("*.pdf"), Condition(ConditionKind.SIZE, "larger", 1000))

    assert auto.matches(rule(*both), facts(), NOW) is False
    assert auto.matches(rule(*both, match=Match.ANY), facts(), NOW) is True


def test_an_extension_is_matched_whole_at_the_end():
    pdf = Condition(ConditionKind.TYPE, "is", ".gz")

    assert auto.matches(rule(pdf), facts("a.tar.gz", content_type=""), NOW)
    assert not auto.matches(rule(pdf), facts("a.gz.txt", content_type=""), NOW)


# Names


def test_rename_template():
    one = facts("IMG_0042.jpg")
    stamp = time.strftime("%Y-%m-%d %H.%M", time.localtime(one.modified))

    assert auto.render_name("{date}.{ext}", one) == f"{stamp}.jpg"
    assert auto.render_name("{name} copy.{ext}", one) == "IMG_0042 copy.jpg"
    assert auto.render_name("{name}", facts("a.tar.gz")) == "a"
    assert auto.render_name("{ext}", facts("a.tar.gz")) == "tar.gz"


def test_temporary_names():
    assert auto.is_temporary("movie.mkv.part")
    assert auto.is_temporary("Photo.CRDOWNLOAD")
    assert auto.is_temporary(".filehouse-1234.part")
    assert not auto.is_temporary("partition.txt")


# Checking a rule


def test_a_good_rule_has_no_problems(tmp_path):
    good = rule(name_is("*.pdf"), actions=[Action(ActionKind.RENAME, "{date}.{ext}")])

    assert auto.check_rule(good, gfile(tmp_path)) == []


@pytest.mark.parametrize(
    "bad",
    [
        rule(actions=[Action(ActionKind.TRASH)]),  # no condition
        rule(name_is("*")),  # no action
        rule(name_is("[", "regex"), actions=[Action(ActionKind.TRASH)]),
        rule(name_is("*"), actions=[Action(ActionKind.RENAME, "{nope}")]),
        rule(name_is("*"), actions=[Action(ActionKind.RENAME, "a/b")]),
        rule(name_is("*"), actions=[Action(ActionKind.RENAME, "")]),
        rule(name_is("*"), actions=[Action(ActionKind.MOVE, "relative/place")]),
        rule(name_is("*"), actions=[Action(ActionKind.MOVE, "")]),
        rule(Condition(ConditionKind.SIZE, "larger", "big"), actions=[Action(ActionKind.TRASH)]),
        rule(Condition(ConditionKind.AGE, "sooner", 3), actions=[Action(ActionKind.TRASH)]),
        Rule("", (), ()),
    ],
)
def test_rules_that_cannot_work(bad, tmp_path):
    assert auto.check_rule(bad, gfile(tmp_path))


def test_nothing_can_follow_a_move_or_a_trash(tmp_path):
    for leaving in (move_to(tmp_path / "out"), Action(ActionKind.TRASH)):
        bad = rule(name_is("*"), actions=[leaving, Action(ActionKind.RENAME, "x")])

        problems = auto.check_rule(bad, gfile(tmp_path))

        assert any("nothing can follow" in problem for problem in problems)


def test_a_destination_that_is_the_rules_own_folder(tmp_path):
    bad = rule(name_is("*"), actions=[move_to(tmp_path)])

    assert auto.check_rule(bad, gfile(tmp_path))
    assert auto.check_rule(bad, gfile(tmp_path / "other")) == []


def test_a_destination_that_cannot_be_written(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root can write anywhere")
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        bad = rule(name_is("*"), actions=[move_to(locked / "sub")])

        assert auto.check_rule(bad, gfile(tmp_path / "in"))
    finally:
        locked.chmod(0o700)


def test_a_destination_that_does_not_exist_is_fine_when_it_can_be_made(tmp_path):
    good = rule(name_is("*"), actions=[move_to(tmp_path / "new" / "deeper")])

    assert auto.check_rule(good, gfile(tmp_path / "in")) == []


# What a run would do


def test_candidates_are_regular_visible_files_only(tmp_path):
    folder = tmp_path / "in"
    make_tree(
        folder, {"a.txt": "a", ".hidden": "h", "sub": {"b.txt": "b"}, "link": ("link", "a.txt")}
    )

    found = [facts.name for _file, facts in auto.candidates(gfile(folder))]

    assert found == ["a.txt"]


def test_a_run_plans_each_matching_file(tmp_path):
    folder = folder_with(tmp_path, ["invoice 1.pdf", "photo.jpg", "invoice 2.pdf"])
    out = tmp_path / "Invoices"
    moving = rule(name_is("invoice*.pdf"), actions=[move_to(out)])

    plan = auto.plan_run(gfile(folder), [moving], NOW)

    assert names(plan.files) == ["invoice 1.pdf", "invoice 2.pdf"]
    step = plan.files[0].steps[0]
    assert step.kind is ActionKind.MOVE
    assert step.folder.get_path() == str(out)
    assert step.new_folder is True
    assert plan.step_count == 2


def test_a_run_changes_nothing(tmp_path):
    folder = folder_with(tmp_path, ["a.pdf"])
    moving = rule(name_is("*.pdf"), actions=[move_to(tmp_path / "out")])

    auto.plan_run(gfile(folder), [moving], NOW)

    assert (folder / "a.pdf").exists()
    assert not (tmp_path / "out").exists()


def test_a_later_rule_sees_the_name_an_earlier_one_gave(tmp_path):
    folder = folder_with(tmp_path, ["IMG_1.jpg"])
    out = tmp_path / "Photos"
    rename = rule(name_is("IMG_*"), actions=[Action(ActionKind.RENAME, "photo.{ext}")], name="a")
    move = rule(name_is("photo.jpg"), actions=[move_to(out)], name="b")

    plan = auto.plan_run(gfile(folder), [rename, move], NOW)

    kinds = [(step.kind, step.name) for step in plan.files[0].steps]
    assert kinds == [(ActionKind.RENAME, "IMG_1.jpg"), (ActionKind.MOVE, "photo.jpg")]


def test_a_move_ends_the_file(tmp_path):
    folder = folder_with(tmp_path, ["a.pdf"])
    first = rule(name_is("*.pdf"), actions=[move_to(tmp_path / "one")], name="first")
    second = rule(name_is("*.pdf"), actions=[move_to(tmp_path / "two")], name="second")

    plan = auto.plan_run(gfile(folder), [first, second], NOW)

    assert [step.rule for step in plan.files[0].steps] == ["first"]


def test_a_rename_does_not_end_the_file_unless_the_rule_stops(tmp_path):
    folder = folder_with(tmp_path, ["a.pdf"])

    def renaming(stop):
        return rule(
            name_is("*.pdf"), actions=[Action(ActionKind.RENAME, "x.pdf")], name="one", stop=stop
        )

    trashing = rule(name_is("*"), actions=[Action(ActionKind.TRASH)], name="two")

    both = auto.plan_run(gfile(folder), [renaming(False), trashing], NOW)
    only = auto.plan_run(gfile(folder), [renaming(True), trashing], NOW)

    assert [step.rule for step in both.files[0].steps] == ["one", "two"]
    assert [step.rule for step in only.files[0].steps] == ["one"]


def test_a_copy_keeps_the_file_in_the_run(tmp_path):
    folder = folder_with(tmp_path, ["a.pdf"])
    copying = rule(
        name_is("*.pdf"),
        actions=[Action(ActionKind.COPY, str(tmp_path / "c")), Action(ActionKind.TRASH)],
    )

    plan = auto.plan_run(gfile(folder), [copying], NOW)

    assert [step.kind for step in plan.files[0].steps] == [ActionKind.COPY, ActionKind.TRASH]


def test_a_rename_to_the_same_name_is_no_step(tmp_path):
    folder = folder_with(tmp_path, ["a.pdf"])
    same = rule(name_is("*"), actions=[Action(ActionKind.RENAME, "{name}.{ext}")])

    assert auto.plan_run(gfile(folder), [same], NOW).files == []


def test_files_still_being_written_wait_for_the_next_run(tmp_path):
    folder = folder_with(tmp_path, ["done.pdf", "movie.mkv.part", "fresh.pdf"])
    os.utime(folder / "fresh.pdf", (NOW - 3, NOW - 3))
    trashing = rule(name_is("*"), actions=[Action(ActionKind.TRASH)])

    plan = auto.plan_run(gfile(folder), [trashing], NOW)

    assert names(plan.files) == ["done.pdf"]
    assert sorted(file.get_basename() for file in plan.still_changing) == [
        "fresh.pdf",
        "movie.mkv.part",
    ]


def test_age_rules_look_at_old_files_only(tmp_path):
    folder = folder_with(tmp_path, ["old.png"], age_days=40)
    make_tree(folder, {"new.png": "n"})
    os.utime(folder / "new.png", (NOW - 2 * DAY, NOW - 2 * DAY))
    trashing = rule(Condition(ConditionKind.AGE, "older", 30), actions=[Action(ActionKind.TRASH)])

    plan = auto.plan_run(gfile(folder), [trashing], NOW)

    assert names(plan.files) == ["old.png"]


def test_a_rule_that_cannot_work_is_left_out_whole(tmp_path):
    folder = folder_with(tmp_path, ["a.pdf"])
    broken = rule(name_is("*"), actions=[Action(ActionKind.RENAME, "{nope}")], name="broken")
    fine = rule(name_is("*"), actions=[Action(ActionKind.TRASH)], name="fine")

    plan = auto.plan_run(gfile(folder), [broken, fine], NOW)

    assert list(plan.problems) == ["broken"]
    assert [step.rule for step in plan.files[0].steps] == ["fine"]


def test_a_disabled_rule_is_not_run(tmp_path):
    folder = folder_with(tmp_path, ["a.pdf"])
    off = rule(name_is("*"), actions=[Action(ActionKind.TRASH)], enabled=False)

    plan = auto.plan_run(gfile(folder), [off], NOW)

    assert plan.files == [] and plan.problems == {}


def test_a_run_on_arrival_looks_only_at_what_arrived(tmp_path):
    folder = folder_with(tmp_path, ["a.pdf", "b.pdf", "c.pdf"])
    trash = rule(name_is("*.pdf"), actions=[Action(ActionKind.TRASH)])

    plan = auto.plan_run(gfile(folder), [trash], NOW, names={"b.pdf", "gone.pdf"})

    assert names(plan.files) == ["b.pdf"]


def test_a_run_on_arrival_leaves_out_rules_that_look_at_age(tmp_path):
    # A copy keeps the age of its source: an old file can arrive this moment
    folder = folder_with(tmp_path, ["a.pdf"], age_days=90)
    old = Condition(ConditionKind.AGE, "older", 30)
    by_age = rule(name_is("*.pdf"), old, actions=[Action(ActionKind.TRASH)], name="age")
    by_name = rule(name_is("*.pdf"), actions=[move_to(tmp_path / "out")], name="name")

    plan = auto.plan_run(gfile(folder), [by_age, by_name], NOW, names={"a.pdf"})

    assert [step.rule for step in plan.files[0].steps] == ["name"]
    assert auto.has_age(by_age) and not auto.has_age(by_name)


# Storing


def test_rules_survive_being_stored():
    original = [
        rule(
            name_is("*.pdf"),
            Condition(ConditionKind.AGE, "older", 30),
            actions=[Action(ActionKind.RENAME, "{date}.{ext}"), Action(ActionKind.TRASH)],
            name="Old",
            match=Match.ANY,
            enabled=False,
            stop=True,
        )
    ]

    assert auto.rules_from_json(auto.rules_to_json(original)) == original


def test_stored_rules_that_were_edited_by_hand_are_not_trusted():
    good = auto.rule_to_json(rule(name_is("*"), actions=[Action(ActionKind.TRASH)]))
    stored = [good, {"name": "x"}, {**good, "match": "some"}, "text", None, {**good, "actions": 3}]

    assert len(auto.rules_from_json(stored)) == 1
    assert auto.rules_from_json({"not": "a list"}) == []
