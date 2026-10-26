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


def runner_for(jobs, stored, clock=None, enabled=lambda: True, check_ms=60_000):
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
        clock=clock,
    )


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
    runner = runner_for(bare_jobs, {uri: [pdfs_to(tmp_path / "out")]}, clock, check_ms=20)
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
