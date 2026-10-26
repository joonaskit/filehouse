"""The rules of folders running by themselves, and where they are kept."""

import json
import os
import time

from ui_helpers import TIMEOUT, gfile

from filehouse.core import automations as auto
from filehouse.core.operations import JobKind, JobState
from filehouse.qt_models.rules_runner import RulesRunner
from filehouse.ui import settings

DAY = auto.DAY_SECONDS


def pdfs_to(folder):
    return auto.Rule(
        "pdfs",
        (auto.Condition(auto.ConditionKind.NAME, "glob", "*.pdf"),),
        (auto.Action(auto.ActionKind.MOVE, str(folder)),),
    )


def settled(folder, *names):
    """Files the rules can act on: changed long enough ago to be whole."""
    folder.mkdir(exist_ok=True)
    for name in names:
        (folder / name).write_text(name)
        os.utime(folder / name, (time.time() - 60, time.time() - 60))
    return gfile(folder).get_uri()


def ended(jobs):
    return bool(jobs.jobs) and jobs.jobs[0].state.is_final


class Clock:
    def __init__(self):
        self.now = time.time()
        self.last = 0.0

    def __call__(self):
        return self.now


def runner_for(jobs, stored, clock=None, enabled=lambda: True, check_ms=60_000, arrival_ms=50):
    clock = clock or Clock()

    def set_last(when):
        clock.last = when

    return RulesRunner(
        jobs,
        lambda: stored,
        enabled,
        lambda: clock.last,
        set_last,
        check_ms=check_ms,
        arrival_ms=arrival_ms,
        clock=clock,
    )


def arrive(tmp_path, folder, name):
    """Move a file that has settled into `folder`, as a finished download is."""
    staging = tmp_path / "staging"
    settled(staging, name)
    (staging / name).rename(folder / name)


def rule_runs(jobs):
    return [job for job in jobs.jobs if job.kind is JobKind.RULES and not job.is_undo]


def all_ended(jobs):
    return bool(jobs.jobs) and all(job.state.is_final for job in jobs.jobs)


def test_rules_run_when_the_process_starts(qtbot, bare_jobs, tmp_path):
    uri = settled(tmp_path / "in", "a.pdf", "b.txt")
    clock = Clock()
    runner = runner_for(bare_jobs, {uri: [pdfs_to(tmp_path / "out")]}, clock)

    runner.start()

    qtbot.waitUntil(lambda: ended(bare_jobs), timeout=TIMEOUT)
    job = bare_jobs.jobs[0]
    assert job.kind is JobKind.RULES and job.state is JobState.DONE
    assert os.listdir(tmp_path / "out") == ["a.pdf"]
    assert clock.last == clock.now


def test_no_job_when_nothing_matches(qtbot, bare_jobs, tmp_path):
    uri = settled(tmp_path / "in", "b.txt")
    runner = runner_for(bare_jobs, {uri: [pdfs_to(tmp_path / "out")]})

    runner.start()

    qtbot.wait(200)
    assert bare_jobs.jobs == []


def test_rules_do_not_run_when_they_are_switched_off(qtbot, bare_jobs, tmp_path):
    uri = settled(tmp_path / "in", "a.pdf")
    clock = Clock()
    runner = runner_for(bare_jobs, {uri: [pdfs_to(tmp_path / "out")]}, clock, lambda: False)

    runner.start()

    qtbot.wait(200)
    assert bare_jobs.jobs == [] and clock.last == 0.0
    assert os.listdir(tmp_path / "in") == ["a.pdf"]


def test_rules_run_again_once_a_day_has_passed(qtbot, bare_jobs, tmp_path):
    uri = settled(tmp_path / "in")
    clock = Clock()
    # Its arrival is not looked at before the day has passed
    runner = runner_for(
        bare_jobs, {uri: [pdfs_to(tmp_path / "out")]}, clock, check_ms=20, arrival_ms=60_000
    )
    runner.start()
    # The run at the start has found nothing before the file comes
    qtbot.waitUntil(lambda: not runner._busy, timeout=TIMEOUT)
    settled(tmp_path / "in", "a.pdf")

    clock.now += DAY - 60
    qtbot.wait(150)
    assert bare_jobs.jobs == []

    clock.now += 120
    qtbot.waitUntil(lambda: ended(bare_jobs), timeout=TIMEOUT)
    assert os.listdir(tmp_path / "out") == ["a.pdf"]
    assert clock.last == clock.now


def test_a_folder_that_is_gone_is_passed_over(qtbot, bare_jobs, tmp_path):
    gone = gfile(tmp_path / "gone").get_uri()
    uri = settled(tmp_path / "in", "a.pdf")
    runner = runner_for(
        bare_jobs, {gone: [pdfs_to(tmp_path / "x")], uri: [pdfs_to(tmp_path / "out")]}
    )

    runner.start()

    qtbot.waitUntil(lambda: ended(bare_jobs), timeout=TIMEOUT)
    assert len(bare_jobs.jobs) == 1 and bare_jobs.jobs[0].state is JobState.DONE
    assert not runner._busy


def test_a_folder_is_not_run_twice_at_once(qtbot, bare_jobs, tmp_path):
    uri = settled(tmp_path / "in", "a.pdf")
    rules = [pdfs_to(tmp_path / "out")]
    runner = runner_for(bare_jobs, {uri: rules})

    runner.run(uri, rules)
    runner.run(uri, rules)

    qtbot.waitUntil(lambda: ended(bare_jobs), timeout=TIMEOUT)
    qtbot.wait(100)
    assert len(bare_jobs.jobs) == 1
    assert not runner._busy


def test_rules_are_kept_in_a_file_of_their_own(tmp_path):
    uri = gfile(tmp_path / "in").get_uri()
    rules = [pdfs_to(tmp_path / "out")]

    settings.set_folder_rules(uri, rules)
    settings._rules = None  # as a new process would read it

    assert settings.folder_rules(uri) == rules
    assert settings.all_folder_rules() == {uri: rules}
    stored = json.loads(settings._rules_path().read_text(encoding="utf-8"))
    assert list(stored) == [uri]

    settings.set_folder_rules(uri, [])
    settings._rules = None
    assert settings.folder_rules(uri) == []


def test_rules_that_were_edited_by_hand_are_not_trusted():
    path = settings._rules_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"file:///x": [{"name": "broken"}], "file:///y": 3}', encoding="utf-8")
    settings._rules = None

    assert settings.all_folder_rules() == {}


def test_the_switch_and_the_last_run_are_remembered():
    assert settings.run_rules() is True and settings.rules_last_run() == 0.0

    settings.set_run_rules(False)
    settings.set_rules_last_run(1234.5)

    assert settings.run_rules() is False and settings.rules_last_run() == 1234.5


# Arrivals (#88)


def watching(qtbot, jobs, stored, **options):
    runner = runner_for(jobs, stored, **options)
    runner.watch()
    return runner


def test_a_file_that_arrives_is_acted_on_and_nothing_else(qtbot, bare_jobs, tmp_path):
    uri = settled(tmp_path / "in", "before.pdf")
    watching(qtbot, bare_jobs, {uri: [pdfs_to(tmp_path / "out")]})

    arrive(tmp_path, tmp_path / "in", "a.pdf")

    qtbot.waitUntil(lambda: ended(bare_jobs), timeout=TIMEOUT)
    assert bare_jobs.jobs[0].state is JobState.DONE
    assert os.listdir(tmp_path / "out") == ["a.pdf"]
    # What was there before is left to the runs at the start, once a day and by hand
    assert os.listdir(tmp_path / "in") == ["before.pdf"]


def test_a_change_to_a_file_already_there_is_no_arrival(qtbot, bare_jobs, tmp_path):
    uri = settled(tmp_path / "in", "a.pdf")
    watching(qtbot, bare_jobs, {uri: [pdfs_to(tmp_path / "out")]})

    (tmp_path / "in" / "a.pdf").write_text("changed")

    qtbot.wait(300)
    assert bare_jobs.jobs == []


def test_a_temporary_name_waits_for_its_final_name(qtbot, bare_jobs, tmp_path):
    uri = settled(tmp_path / "in")
    watching(
        qtbot,
        bare_jobs,
        {
            uri: [
                auto.Rule(
                    "all",
                    (auto.Condition(auto.ConditionKind.NAME, "glob", "*"),),
                    (auto.Action(auto.ActionKind.MOVE, str(tmp_path / "out")),),
                )
            ]
        },
    )

    arrive(tmp_path, tmp_path / "in", "a.pdf.part")
    qtbot.wait(300)
    assert bare_jobs.jobs == []

    (tmp_path / "in" / "a.pdf.part").rename(tmp_path / "in" / "a.pdf")
    qtbot.waitUntil(lambda: ended(bare_jobs), timeout=TIMEOUT)
    assert os.listdir(tmp_path / "out") == ["a.pdf"]


def test_a_file_still_being_written_is_acted_on_once_it_settles(qtbot, bare_jobs, tmp_path):
    uri = settled(tmp_path / "in")
    watching(qtbot, bare_jobs, {uri: [pdfs_to(tmp_path / "out")]})

    (tmp_path / "in" / "a.pdf").write_text("being written")
    qtbot.wait(300)
    assert bare_jobs.jobs == []

    # No arrival comes of this: the name was kept to be looked at again
    os.utime(tmp_path / "in" / "a.pdf", (time.time() - 60, time.time() - 60))
    qtbot.waitUntil(lambda: ended(bare_jobs), timeout=TIMEOUT)
    assert os.listdir(tmp_path / "out") == ["a.pdf"]


def test_rules_that_move_a_file_back_and_forth_stop(qtbot, bare_jobs, tmp_path):
    there = settled(tmp_path / "a")
    back = settled(tmp_path / "b")
    watching(qtbot, bare_jobs, {there: [pdfs_to(tmp_path / "b")], back: [pdfs_to(tmp_path / "a")]})

    arrive(tmp_path, tmp_path / "a", "x.pdf")

    qtbot.waitUntil(lambda: all_ended(bare_jobs), timeout=TIMEOUT)
    qtbot.wait(400)
    assert len(rule_runs(bare_jobs)) == 1
    assert os.listdir(tmp_path / "b") == ["x.pdf"] and os.listdir(tmp_path / "a") == []


def test_a_rename_by_a_rule_is_not_an_arrival(qtbot, bare_jobs, tmp_path):
    uri = settled(tmp_path / "in")
    renames = auto.Rule(
        "stamp",
        (auto.Condition(auto.ConditionKind.NAME, "glob", "*.pdf"),),
        (auto.Action(auto.ActionKind.RENAME, "new {name}.{ext}"),),
    )
    watching(qtbot, bare_jobs, {uri: [renames]})

    arrive(tmp_path, tmp_path / "in", "a.pdf")

    qtbot.waitUntil(lambda: all_ended(bare_jobs), timeout=TIMEOUT)
    qtbot.wait(400)
    assert len(rule_runs(bare_jobs)) == 1
    assert os.listdir(tmp_path / "in") == ["new a.pdf"]


def test_taking_a_run_back_does_not_run_the_rules_again(qtbot, bare_jobs, tmp_path):
    uri = settled(tmp_path / "in")
    watching(qtbot, bare_jobs, {uri: [pdfs_to(tmp_path / "out")]})
    arrive(tmp_path, tmp_path / "in", "a.pdf")
    qtbot.waitUntil(lambda: all_ended(bare_jobs), timeout=TIMEOUT)

    bare_jobs.undo()

    qtbot.waitUntil(lambda: len(bare_jobs.jobs) == 2 and all_ended(bare_jobs), timeout=TIMEOUT)
    qtbot.wait(400)
    assert len(rule_runs(bare_jobs)) == 1
    assert os.listdir(tmp_path / "in") == ["a.pdf"]


def test_folders_are_not_watched_while_rules_are_switched_off(qtbot, bare_jobs, tmp_path):
    uri = settled(tmp_path / "in")
    on = [True]
    runner = watching(qtbot, bare_jobs, {uri: [pdfs_to(tmp_path / "out")]}, enabled=lambda: on[0])
    assert list(runner._watches) == [uri]

    on[0] = False
    runner.watch()
    arrive(tmp_path, tmp_path / "in", "a.pdf")

    qtbot.wait(300)
    assert runner._watches == {} and bare_jobs.jobs == []
