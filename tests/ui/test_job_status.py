"""The status bar's line about the running operations, fed with stand-ins for jobs."""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal
from ui_helpers import gfile

from filehouse.core.operations import JobKind, JobState
from filehouse.core.operations.job import JobProgress
from filehouse.integration import style
from filehouse.qt_models import job_model
from filehouse.ui.job_status import JobStatus


class Jobs(QObject):
    job_added = Signal(object)
    job_changed = Signal(object)
    job_removed = Signal(object)

    def __init__(self):
        super().__init__()
        self.jobs = []

    def add(self, job):
        self.jobs.append(job)
        self.job_added.emit(job)
        return job

    def change(self, job, **fields):
        for name, value in fields.items():
            setattr(job, name, value)
        self.job_changed.emit(job)

    def remove(self, job):
        self.jobs.remove(job)
        self.job_removed.emit(job)


def job(count=4, state=JobState.RUNNING, done=0, total=100, kind=JobKind.COPY, summary=""):
    return SimpleNamespace(
        kind=kind,
        sources=[gfile(f"/tmp/file{index}") for index in range(count)],
        destination=gfile("/tmp/Pictures"),
        state=state,
        progress=JobProgress(bytes_done=done, bytes_total=total, planned=total is not None),
        summary=summary,
    )


def planning():
    return SimpleNamespace(
        kind=JobKind.COPY,
        sources=[gfile("/tmp/a")],
        destination=gfile("/tmp/Pictures"),
        state=JobState.PLANNING,
        progress=JobProgress(),
        summary="",
    )


@pytest.fixture
def jobs():
    return Jobs()


@pytest.fixture
def status(qtbot, jobs):
    status = JobStatus(jobs)
    qtbot.addWidget(status)
    return status


def test_nothing_to_show_without_jobs(status, jobs):
    assert status.isHidden()

    one = jobs.add(job())
    assert not status.isHidden()

    jobs.remove(one)
    assert status.isHidden()


def test_one_job_shows_what_it_does_and_how_far_it_is(status, jobs):
    one = jobs.add(job(count=4, done=62, total=100))

    # Short: where the files go is in the panel
    assert status.title.text() == "Copying 4 items"
    assert status.accessibleName() == "Copying 4 items"
    assert not status.bar.isHidden() and status.bar.value() == 620
    assert status.percent.text() == "62 %"

    jobs.change(one, progress=JobProgress(bytes_done=80, bytes_total=100, planned=True))
    assert status.percent.text() == "80 %"


def test_all_bytes_written_is_not_shown_as_finished(status, jobs):
    jobs.add(job(done=100, total=100))

    assert status.percent.text() == "99 %"
    assert not status.bar.isHidden()


def test_a_job_that_is_still_planning_shows_as_busy(status, jobs):
    jobs.add(planning())

    assert status.title.text() == "Copying “a”"
    assert status.bar.maximum() == 0
    assert status.percent.isHidden()


def test_several_jobs_show_their_count_and_progress_together(status, jobs):
    jobs.add(job(done=10, total=100))
    jobs.add(job(done=150, total=300))

    assert status.title.text() == "2 operations"
    assert status.percent.text() == "40 %"

    # One that doesn't know its size yet counts as not started
    jobs.add(planning())
    assert status.title.text() == "3 operations"
    assert status.percent.text() == "20 %"


def test_a_failure_is_named_once_nothing_is_running(status, jobs):
    failed = jobs.add(job(state=JobState.FAILED, summary="Copied 1 of 2 files, 1 skipped"))
    running = jobs.add(job(done=5, total=10))
    assert status.title.text() == "Copying 4 items"

    jobs.change(running, state=JobState.DONE, summary="Copied 4 items")

    assert status.title.text() == "1 operation failed"
    assert style.color("danger").name() in status.title.styleSheet()
    assert status.bar.isHidden() and status.percent.isHidden()

    jobs.remove(failed)
    assert status.title.text() == "Copied 4 items"


def test_how_the_last_job_ended_stays_on_show(status, jobs):
    one = jobs.add(job(done=5, total=10))

    jobs.change(one, state=JobState.DONE, summary="Copied 4 items")
    assert status.title.text() == "Copied 4 items"
    assert style.color("success").name() in status.title.styleSheet()
    assert status.bar.isHidden()

    jobs.change(one, state=JobState.CANCELLED, summary="Cancelled after 2 of 4 files")
    assert status.title.text() == "Cancelled after 2 of 4 files"
    assert status.title.styleSheet() == ""


def test_headings_short_and_long():
    assert job_model.short_heading(job(count=1)) == "Copying “file0”"
    assert job_model.heading(job(count=3, kind=JobKind.MOVE)) == "Moving 3 items to Pictures"
    assert job_model.short_heading(job(count=3, kind=JobKind.MOVE)) == "Moving 3 items"
    for kind in (JobKind.TRASH, JobKind.RESTORE, JobKind.DELETE):
        assert job_model.short_heading(job(kind=kind)) == job_model.heading(job(kind=kind))


def test_overall_fraction():
    assert job_model.overall_fraction([]) is None
    assert job_model.overall_fraction([planning()]) is None
    assert job_model.overall_fraction([job(done=1, total=4)]) == 0.25
    # By bytes, so a large job weighs more than a small one
    assert job_model.overall_fraction([job(done=10, total=10), job(done=0, total=90)]) == 0.1
    # Counted by files when there are no bytes (a trash job): the mean of the two
    by_files = job(total=0)
    by_files.progress = JobProgress(files_done=1, files_total=2, planned=True)
    assert job_model.overall_fraction([by_files, job(done=10, total=10)]) == 0.75
