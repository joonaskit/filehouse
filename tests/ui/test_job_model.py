import os
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication
from ui_helpers import gfile

from filehouse.core.operations import Answer, Conflict, JobKind, JobProgress, JobState
from filehouse.qt_models import job_model
from filehouse.qt_models.job_model import JobModel


@pytest.fixture
def files(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "dst").mkdir()
    (tmp_path / "src" / "a.txt").write_text("aaa")
    return tmp_path


def test_a_job_runs_on_a_worker_and_reports_on_the_ui_thread(qtbot, jobs, files, wait_final):
    threads, states, added = set(), [], []
    jobs.job_added.connect(added.append)
    jobs.job_changed.connect(lambda job: threads.add(QThread.currentThread()))
    jobs.job_changed.connect(lambda job: states.append(job.state))

    with qtbot.waitSignal(jobs.idle, timeout=5000):
        job = jobs.start(JobKind.COPY, [gfile(files / "src" / "a.txt")], gfile(files / "dst"))
        assert jobs.active_count() == 1

    assert added == [job]
    assert threads == {QApplication.instance().thread()}
    assert states[-1] is JobState.DONE
    assert (files / "dst" / "a.txt").read_text() == "aaa"
    assert jobs.jobs == [job]
    assert jobs.active_count() == 0


def test_finished_jobs_leave_after_a_while_and_failed_ones_stay(qtbot, bare_jobs, files):
    jobs = bare_jobs
    jobs._keep_finished_ms = 50
    jobs.question_asked.connect(lambda job, question: question.answer(Answer.SKIP))
    removed = []
    jobs.job_removed.connect(removed.append)

    done = jobs.start(JobKind.COPY, [gfile(files / "src" / "a.txt")], gfile(files / "dst"))
    failed = jobs.start(JobKind.COPY, [gfile(files / "src" / "missing")], gfile(files / "dst"))

    qtbot.waitUntil(lambda: removed == [done], timeout=5000)
    qtbot.wait(100)
    assert jobs.jobs == [failed]
    assert failed.state is JobState.FAILED

    jobs.dismiss(failed)
    assert jobs.jobs == []
    assert removed == [done, failed]


def test_a_running_job_cannot_be_dismissed(qtbot, jobs, files, wait_question):
    (files / "dst" / "a.txt").write_text("old")
    job = jobs.start(JobKind.COPY, [gfile(files / "src" / "a.txt")], gfile(files / "dst"))
    wait_question(jobs)

    jobs.dismiss(job)

    assert jobs.jobs == [job]


def test_a_question_reaches_the_ui_thread_and_its_answer_the_job(
    qtbot, bare_jobs, files, wait_final
):
    (files / "dst" / "a.txt").write_text("old")
    jobs = bare_jobs
    asked = []

    def answer(job, question):
        assert QThread.currentThread() is QApplication.instance().thread()
        assert question.job is job and job.state is JobState.WAITING
        asked.append(question)
        question.answer(Answer.REPLACE)

    jobs.question_asked.connect(answer)

    job = jobs.start(JobKind.COPY, [gfile(files / "src" / "a.txt")], gfile(files / "dst"))
    wait_final(jobs, job)

    assert [type(question) for question in asked] == [Conflict]
    assert job.state is JobState.DONE
    assert (files / "dst" / "a.txt").read_text() == "aaa"


def test_cancel_ends_a_job_that_waits_for_an_answer(qtbot, bare_jobs, files, wait_final):
    (files / "dst" / "a.txt").write_text("old")
    jobs = bare_jobs
    asked = []
    jobs.question_asked.connect(lambda job, question: asked.append(question))
    job = jobs.start(JobKind.COPY, [gfile(files / "src" / "a.txt")], gfile(files / "dst"))
    qtbot.waitUntil(lambda: bool(asked), timeout=5000)

    jobs.cancel_all()
    wait_final(jobs, job)

    assert job.state is JobState.CANCELLED
    assert (files / "dst" / "a.txt").read_text() == "old"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permissions")
def test_retry_puts_a_new_job_in_place_of_the_failed_one(qtbot, bare_jobs, files, wait_final):
    jobs = bare_jobs
    locked = files / "src" / "a.txt"
    locked.chmod(0)
    jobs.question_asked.connect(lambda job, question: question.answer(Answer.SKIP))
    failed = wait_final(jobs, jobs.start(JobKind.COPY, [gfile(locked)], gfile(files / "dst")))
    assert failed.state is JobState.FAILED
    assert job_model.text(failed) == "a.txt: permission denied"

    locked.chmod(0o600)
    again = jobs.retry(failed)
    wait_final(jobs, again)

    assert jobs.jobs == [again]
    assert again.state is JobState.DONE
    assert (files / "dst" / "a.txt").read_text() == "aaa"


def test_titles_and_texts(qtbot, jobs, files, wait_final, wait_question):
    one = [gfile(files / "src" / "a.txt")]
    two = [*one, gfile(files / "src" / "b.txt")]
    (files / "dst" / "a.txt").write_text("old")

    copy = jobs.start(JobKind.COPY, one, gfile(files / "dst"))
    box = wait_question(jobs)
    assert job_model.title(copy) == "Copying “a.txt” to dst"
    assert job_model.text(copy) == "Waiting for your answer"
    box.button_for(Answer.SKIP).click()
    wait_final(jobs, copy)
    assert job_model.title(copy) == "Copied 0 files"
    assert job_model.text(copy) == ""

    move = jobs.start(JobKind.MOVE, two, gfile(files))
    assert job_model.title(move) == f"Moving 2 items to {files.name}"
    assert job_model.title(jobs.start(JobKind.TRASH, two)) == "Moving 2 items to the trash"
    assert job_model.title(jobs.start(JobKind.DELETE, two)) == "Deleting 2 items"


def test_a_model_that_goes_away_cancels_its_waiting_jobs(qtbot, files):
    (files / "dst" / "a.txt").write_text("old")
    model = JobModel()
    job = model.start(JobKind.COPY, [gfile(files / "src" / "a.txt")], gfile(files / "dst"))
    qtbot.waitUntil(lambda: job.state is JobState.WAITING, timeout=5000)

    model.deleteLater()

    qtbot.waitUntil(lambda: job.state is JobState.CANCELLED, timeout=5000)
    assert (files / "dst" / "a.txt").read_text() == "old"


def test_speed_is_measured_over_the_last_few_seconds(qtbot, bare_jobs, files, monkeypatch):
    jobs = bare_jobs
    job = jobs.start(JobKind.TRASH, [])
    clock = [100.0]
    monkeypatch.setattr(job_model.time, "monotonic", lambda: clock[0])

    class Running:
        id = job.id
        state = JobState.RUNNING
        progress = None

    def report(seconds, megabytes):
        clock[0] = 100.0 + seconds
        Running.progress = type("P", (), {"bytes_done": megabytes * 1_000_000})
        jobs._sample(Running)

    report(0, 0)
    assert jobs.speed(Running) is None
    report(0.2, 1)
    # Too little time to tell
    assert jobs.speed(Running) is None
    report(1, 10)
    assert jobs.speed(Running) == 10_000_000
    report(10, 30)
    report(11, 50)
    # Only the recent past counts
    assert jobs.speed(Running) == 20_000_000
    Running.state = JobState.WAITING
    assert jobs.speed(Running) is None


def test_running_text_names_the_file_its_position_and_the_speed(qtbot):
    class Job:
        state = JobState.RUNNING
        progress = type("P", (), {"current": "film.mkv", "files_done": 2, "files_total": 12})

    assert job_model.text(Job) == "film.mkv (3 of 12)"
    assert job_model.text(Job, 2_500_000).startswith("film.mkv (3 of 12), 2")
    assert job_model.text(Job, 2_500_000).endswith("/s")


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permissions")
def test_a_retry_is_added_before_the_job_it_replaces_is_removed(
    qtbot, bare_jobs, files, wait_final
):
    jobs = bare_jobs
    jobs.question_asked.connect(lambda job, question: question.answer(Answer.SKIP))
    locked = files / "src" / "a.txt"
    locked.chmod(0)
    failed = wait_final(jobs, jobs.start(JobKind.COPY, [gfile(locked)], gfile(files / "dst")))
    locked.chmod(0o600)
    events = []
    jobs.job_added.connect(lambda job: events.append(("added", job, jobs.predecessor(job))))
    jobs.job_removed.connect(lambda job: events.append(("removed", job)))

    again = jobs.retry(failed)

    assert events == [("added", again, failed), ("removed", failed)]
    wait_final(jobs, again)


def test_a_report_held_back_is_sent_once_its_interval_is_over(qtbot, bare_jobs):
    jobs = bare_jobs
    job = SimpleNamespace(
        id=-1, state=JobState.RUNNING, progress=JobProgress(current="one"), cancel=lambda: None
    )
    jobs._jobs.append(job)
    seen = []
    jobs.job_changed.connect(lambda job: seen.append(job.progress.current))

    jobs._report(job)
    job.progress = JobProgress(current="two")
    # Too soon after the first to be sent, and then nothing more comes
    jobs._report(job)
    assert seen == ["one"]

    qtbot.waitUntil(lambda: seen == ["one", "two"], timeout=1000)
    jobs._jobs.remove(job)


def test_a_run_of_rules_is_one_job_and_one_undo(qtbot, jobs, tmp_path):
    import time

    from filehouse.core import automations as auto

    (tmp_path / "in").mkdir()
    for name in ("a.pdf", "b.pdf"):
        (tmp_path / "in" / name).write_text(name)
    moving = auto.Rule(
        "pdfs",
        (auto.Condition(auto.ConditionKind.NAME, "glob", "*.pdf"),),
        (auto.Action(auto.ActionKind.MOVE, str(tmp_path / "out")),),
    )
    plan = auto.plan_run(gfile(tmp_path / "in"), [moving], time.time() + 60)
    added = []
    jobs.job_added.connect(added.append)

    with qtbot.waitSignal(jobs.idle, timeout=5000):
        job = jobs.run_rules(gfile(tmp_path / "in"), plan)
        assert job_model.heading(job) == "Running rules on 2 files in in"

    assert added == [job] and job.state is JobState.DONE
    assert sorted(os.listdir(tmp_path / "out")) == ["a.pdf", "b.pdf"]
    assert jobs.undo_text() == "folder rules in “in”"

    with qtbot.waitSignal(jobs.idle, timeout=5000):
        undone = jobs.undo()
        assert job_model.heading(undone.job) == "Taking back folder rules"

    assert undone.job.state is JobState.DONE
    assert sorted(os.listdir(tmp_path / "in")) == ["a.pdf", "b.pdf"]
    assert not (tmp_path / "out").exists()
    assert jobs.undo_text() is None
