"""The reporter against a stand-in for the island and stand-ins for jobs."""

import pytest
from gi.repository import Gio
from PySide6.QtCore import QObject, Signal

from filehouse.core.errors import OperationError
from filehouse.core.operations import JobKind, JobProgress, JobState
from filehouse.integration.island import IslandReporter

INTERVAL_MS = 60


class FakeJob:
    _ids = iter(range(1, 10_000))

    def __init__(self, kind=JobKind.COPY, names=("a.txt", "b.txt")):
        self.id = next(self._ids)
        self.kind = kind
        self.sources = [Gio.File.new_for_path(f"/src/{name}") for name in names]
        self.destination = Gio.File.new_for_path("/home/me/Pictures")
        self.state = JobState.QUEUED
        self.progress = JobProgress()
        self.summary = ""
        self.errors = []
        self.started = True
        self.can_retry = False
        self.cancel_requested = False

    @property
    def error(self):
        return self.errors[0] if self.errors else None


class FakeJobs(QObject):
    """What the reporter uses of JobModel."""

    job_added = Signal(object)
    job_changed = Signal(object)
    job_removed = Signal(object)

    def __init__(self):
        super().__init__()
        self.jobs = []
        self.predecessors = {}
        self.retried = []

    def add(self, job=None, **kwargs):
        job = job or FakeJob(**kwargs)
        self.jobs.append(job)
        self.job_added.emit(job)
        return job

    def change(self, job, state=None, **attributes):
        if state is not None:
            job.state = state
        for name, value in attributes.items():
            setattr(job, name, value)
        self.job_changed.emit(job)

    def run(self, job, fraction=None, current="a.txt"):
        """Make the job running, at `fraction` of its bytes."""
        progress = JobProgress()
        if fraction is not None:
            progress = JobProgress(
                files_total=2, bytes_done=round(fraction * 1000), bytes_total=1000, planned=True
            )
        self.change(
            job, JobState.RUNNING, progress=JobProgress(**_fields(progress), current=current)
        )

    def predecessor(self, job):
        return self.predecessors.get(job.id)

    def speed(self, job):
        return None

    def cancel(self, job):
        job.cancel_requested = True

    def retry(self, job):
        again = FakeJob()
        self.retried.append((job, again))
        self.predecessors[again.id] = job
        self.add(again)
        self.dismiss(job)
        return again

    def dismiss(self, job):
        if job in self.jobs:
            self.jobs.remove(job)
            self.job_removed.emit(job)


def _fields(progress):
    names = ("files_done", "files_total", "bytes_done", "bytes_total", "planned")
    return {name: getattr(progress, name) for name in names}


class FakeIsland:
    """Records the calls. Replies with success at once unless told otherwise."""

    def __init__(self, available=True):
        self.available = available
        self.calls = []  # (method, id, fields)
        self.replies = []  # error codes for the next calls, None for success
        self.held = []  # replies not given yet, when `hold` is set
        self.hold = False

    def _call(self, method, activity_id, fields, done):
        if not self.available:
            return
        self.calls.append((method, activity_id, dict(fields or {})))
        code = self.replies.pop(0) if self.replies else None
        if done is None:
            return
        if self.hold:
            self.held.append(lambda: done(code))
        else:
            done(code)

    def show(self, activity_id, fields, done=None):
        self._call("Show", activity_id, fields, done)

    def update(self, activity_id, fields, done=None):
        self._call("Update", activity_id, fields, done)

    def dismiss(self, activity_id, done=None):
        self._call("Dismiss", activity_id, None, done)

    def release(self):
        held, self.held = self.held, []
        for reply in held:
            reply()

    def appear(self):
        self.available = True
        self.on_available()

    def vanish(self):
        self.available = False
        self.on_unavailable()

    def methods(self):
        return [method for method, _id, _fields in self.calls]

    def state_of(self, activity_id):
        """The fields the island would hold for an activity, from every call so far."""
        merged = None
        for method, call_id, fields in self.calls:
            if call_id != activity_id:
                continue
            if method == "Show":
                merged = dict(fields)
            elif method == "Update" and merged is not None:
                merged.update(fields)
            elif method == "Dismiss":
                merged = None
        return merged


@pytest.fixture
def setup(qtbot):
    jobs, island = FakeJobs(), FakeIsland()
    reporter = IslandReporter(jobs, island, interval_ms=INTERVAL_MS)
    return jobs, island, reporter


def settle(qtbot):
    """Long enough for held-back updates to go out."""
    qtbot.wait(INTERVAL_MS * 3)


# Mapping


def test_a_queued_job_is_not_published_until_it_starts(qtbot, setup):
    jobs, island, _ = setup
    job = jobs.add()
    jobs.change(job)
    assert island.calls == []

    jobs.change(job, JobState.PLANNING)

    assert island.calls == [
        (
            "Show",
            f"job-{job.id}",
            {
                "title": "Copying 2 items to Pictures",
                "text": "Preparing…",
                "icon": "edit-copy",
                "state": "running",
                "progress": -1.0,
                "actions": [("cancel", "Cancel")],
            },
        )
    ]


@pytest.mark.parametrize(
    "kind, icon, title",
    [
        (JobKind.MOVE, "transform-move", "Moving 2 items to Pictures"),
        (JobKind.TRASH, "user-trash", "Moving 2 items to the trash"),
        (JobKind.DELETE, "edit-delete", "Deleting 2 items"),
    ],
)
def test_title_and_icon_by_kind(qtbot, setup, kind, icon, title):
    jobs, island, _ = setup
    job = jobs.add(kind=kind)

    jobs.change(job, JobState.PLANNING)

    fields = island.calls[0][2]
    assert (fields["icon"], fields["title"]) == (icon, title)


def test_running_reports_progress_and_the_current_file(qtbot, setup):
    jobs, island, _ = setup
    job = jobs.add()
    jobs.change(job, JobState.PLANNING)

    jobs.run(job, 0.25, current="holiday.jpg")
    settle(qtbot)

    assert island.methods() == ["Show", "Update"]
    # Only what changed is sent
    assert island.calls[1][2] == {"text": "holiday.jpg (1 of 2)", "progress": 0.25}


def test_waiting_for_the_user_says_so_and_keeps_the_progress(qtbot, setup):
    jobs, island, _ = setup
    job = jobs.add()
    jobs.run(job, 0.5)

    jobs.change(job, JobState.WAITING)
    settle(qtbot)

    state = island.state_of(f"job-{job.id}")
    assert state["text"] == "Waiting for you in Files"
    assert state["progress"] == 0.5
    assert state["state"] == "running"


def test_done(qtbot, setup):
    jobs, island, _ = setup
    job = jobs.add()
    jobs.run(job, 0.3)
    jobs.run(job, 1.0)
    # The last progress was held back by the throttle
    assert island.state_of(f"job-{job.id}") | {"text": ""} == {
        "icon": "edit-copy",
        "state": "running",
        "title": "Copying 2 items to Pictures",
        "text": "",
        "progress": 0.3,
        "actions": [("cancel", "Cancel")],
    }

    jobs.change(job, JobState.DONE, summary="Copied 2 files")

    assert island.calls[-1] == (
        "Update",
        f"job-{job.id}",
        {"state": "done", "title": "Copied 2 files", "text": "", "progress": 1.0},
    )


def test_full_progress_is_never_reported_as_done(qtbot, setup):
    jobs, island, _ = setup
    job = jobs.add()

    jobs.run(job, 1.0)
    settle(qtbot)

    assert island.state_of(f"job-{job.id}")["progress"] == 1.0
    assert all(fields.get("state", "running") == "running" for _m, _i, fields in island.calls)


def test_cancelled_says_how_far_it_got(qtbot, setup):
    jobs, island, _ = setup
    job = jobs.add()
    jobs.run(job, 0.4)
    jobs.run(job, 0.45)

    jobs.change(job, JobState.CANCELLED, summary="Cancelled after 5 of 12 files")

    assert island.calls[-1][2] == {
        "state": "cancelled",
        "text": "Cancelled after 5 of 12 files",
        "progress": 0.45,
    }
    assert island.state_of(f"job-{job.id}")["title"] == "Copying 2 items to Pictures"


def test_failed_names_the_file_and_offers_retry_and_show(qtbot, setup):
    jobs, island, _ = setup
    job = jobs.add()
    jobs.run(job, 0.4)
    errors = [
        OperationError("/src/report.pdf", "copy", "permission denied"),
        OperationError("/src/b.txt", "copy", "no space left on device"),
    ]

    jobs.change(
        job,
        JobState.FAILED,
        summary="Copied 10 of 12 files, 2 skipped",
        errors=errors,
        can_retry=True,
    )

    assert island.calls[-1][2] == {
        "state": "failed",
        "title": "Copied 10 of 12 files, 2 skipped",
        "text": "",
        "error": "report.pdf: permission denied, and 1 more",
        "actions": [("retry", "Retry"), ("show", "Show")],
    }


def test_a_failure_that_cannot_be_retried_offers_only_show(qtbot, setup):
    jobs, island, _ = setup
    job = jobs.add()
    jobs.run(job)

    jobs.change(job, JobState.FAILED, summary="x", errors=[OperationError("", "copy", "no")])

    assert island.calls[-1][2]["actions"] == [("show", "Show")]


# What is worth showing


def test_a_job_that_was_over_at_once_is_not_shown(qtbot, setup):
    jobs, island, _ = setup

    done = jobs.add()
    jobs.change(done, JobState.DONE, summary="Copied 1 file")
    cancelled = jobs.add()
    jobs.change(cancelled, JobState.CANCELLED, summary="Cancelled before it started", started=False)

    assert island.calls == []


def test_a_job_that_failed_at_once_is_shown(qtbot, setup):
    jobs, island, _ = setup
    job = jobs.add()

    jobs.change(
        job,
        JobState.FAILED,
        summary="Copied 0 of 1 file, 1 skipped",
        errors=[OperationError("/src/a.txt", "copy", "permission denied")],
    )

    assert island.methods() == ["Show"]
    assert island.calls[0][2]["state"] == "failed"
    assert island.calls[0][2]["error"] == "a.txt: permission denied"
    assert "progress" not in island.calls[0][2]


# Throttling


def test_progress_is_throttled_and_the_last_value_arrives(qtbot, setup):
    jobs, island, _ = setup
    job = jobs.add()
    jobs.run(job, 0.0)

    for step in range(1, 41):
        jobs.run(job, step / 100)
    assert len(island.calls) <= 2

    settle(qtbot)
    assert len(island.calls) <= 3
    assert island.state_of(f"job-{job.id}")["progress"] == 0.4


def test_updates_come_at_most_once_per_interval(qtbot, setup):
    jobs, island, _ = setup
    job = jobs.add()
    jobs.run(job, 0.0)

    for step in range(1, 13):
        jobs.run(job, step / 100)
        qtbot.wait(INTERVAL_MS // 4)

    # 12 changes over about 3 intervals
    assert 2 <= len(island.calls) <= 6


def test_the_final_state_is_sent_at_once(qtbot, setup):
    jobs, island, _ = setup
    job = jobs.add()
    jobs.run(job, 0.0)
    jobs.run(job, 0.5)
    jobs.run(job, 0.9)

    jobs.change(job, JobState.DONE, summary="Copied 2 files")

    assert island.calls[-1][2]["state"] == "done"
    count = len(island.calls)
    settle(qtbot)
    # And nothing held back arrives after it
    assert len(island.calls) == count


def test_nothing_is_sent_when_nothing_changed(qtbot, setup):
    jobs, island, _ = setup
    job = jobs.add()
    jobs.run(job, 0.5)

    for _ in range(5):
        jobs.run(job, 0.5)
        settle(qtbot)

    assert island.methods() == ["Show"]


# Actions from the island


def test_cancel_is_a_request_and_cancelled_is_sent_only_when_the_job_has_stopped(qtbot, setup):
    jobs, island, _ = setup
    job = jobs.add()
    jobs.run(job, 0.3)

    island.on_action(f"job-{job.id}", "cancel")
    settle(qtbot)

    assert job.cancel_requested
    assert island.state_of(f"job-{job.id}")["state"] == "running"

    jobs.change(job, JobState.CANCELLED, summary="Cancelled after 1 of 2 files")
    assert island.state_of(f"job-{job.id}")["state"] == "cancelled"


def test_actions_and_dismissals_for_other_apps_are_ignored(qtbot, setup):
    jobs, island, _ = setup
    job = jobs.add()
    jobs.run(job, 0.3)

    island.on_action("copy-1", "cancel")
    island.on_action("job-999999", "cancel")
    island.on_dismissed("something-else")

    assert not job.cancel_requested
    assert jobs.jobs == [job]


def test_retry_starts_a_new_job_shown_under_the_old_id(qtbot, setup):
    jobs, island, _ = setup
    job = jobs.add()
    jobs.run(job, 0.3)
    jobs.change(
        job,
        JobState.FAILED,
        summary="failed",
        errors=[OperationError("/a", "copy", "no")],
        can_retry=True,
    )
    old_id = f"job-{job.id}"
    before = len(island.calls)

    island.on_action(old_id, "retry")

    (_, again) = jobs.retried[0]
    # The failure stays on show while the new job waits its turn
    assert island.calls[before:] == []

    jobs.change(again, JobState.PLANNING)

    # A finished activity can't be updated back to running: it is shown anew
    assert [(method, call_id) for method, call_id, _ in island.calls[before:]] == [("Show", old_id)]
    assert island.state_of(old_id)["state"] == "running"

    jobs.change(again, JobState.DONE, summary="Copied 1 file")
    assert island.state_of(old_id)["state"] == "done"
    assert "Dismiss" not in island.methods()


def test_retry_that_is_over_at_once_still_replaces_the_failure(qtbot, setup):
    jobs, island, _ = setup
    job = jobs.add()
    jobs.run(job)
    jobs.change(job, JobState.FAILED, summary="failed", errors=[OperationError("/a", "copy", "no")])
    job.can_retry = True
    island.on_action(f"job-{job.id}", "retry")
    (_, again) = jobs.retried[0]

    jobs.change(again, JobState.DONE, summary="Copied 1 file")

    assert island.calls[-1][0] == "Show"
    assert island.state_of(f"job-{job.id}")["state"] == "done"


def test_show_asks_the_app_to_come_forward(qtbot, setup):
    jobs, island, reporter = setup
    job = jobs.add()
    jobs.run(job)
    asked = []
    reporter.show_requested.connect(asked.append)

    island.on_action(f"job-{job.id}", "show")

    assert asked == [job]


# Dismissal, both ways


def failed_job(jobs):
    job = jobs.add()
    jobs.run(job)
    jobs.change(job, JobState.FAILED, summary="failed", errors=[OperationError("/a", "copy", "no")])
    return job


def test_dismissing_a_failure_in_the_island_dismisses_it_in_the_app(qtbot, setup):
    jobs, island, _ = setup
    job = failed_job(jobs)

    island.on_dismissed(f"job-{job.id}")

    assert jobs.jobs == []
    # The island already dropped it: nothing is sent back
    assert "Dismiss" not in island.methods()


def test_dismissing_a_failure_in_the_app_dismisses_it_in_the_island(qtbot, setup):
    jobs, island, _ = setup
    job = failed_job(jobs)

    jobs.dismiss(job)

    assert island.calls[-1] == ("Dismiss", f"job-{job.id}", {})


def test_finished_jobs_leave_the_island_by_themselves(qtbot, setup):
    jobs, island, _ = setup
    job = jobs.add()
    jobs.run(job)
    jobs.change(job, JobState.DONE, summary="Copied 2 files")

    jobs.dismiss(job)

    assert "Dismiss" not in island.methods()


def test_a_running_activity_dismissed_in_the_island_is_left_alone(qtbot, setup):
    jobs, island, _ = setup
    job = jobs.add()
    jobs.run(job, 0.1)
    count = len(island.calls)

    island.on_dismissed(f"job-{job.id}")
    jobs.run(job, 0.5)
    jobs.change(job, JobState.DONE, summary="Copied 2 files")
    settle(qtbot)

    assert jobs.jobs == [job]
    assert not job.cancel_requested
    assert len(island.calls) == count


# The island coming and going


def test_without_an_island_nothing_is_sent_and_jobs_are_untouched(qtbot):
    jobs, island = FakeJobs(), FakeIsland(available=False)
    IslandReporter(jobs, island, interval_ms=INTERVAL_MS)
    job = jobs.add()

    jobs.run(job, 0.5)
    jobs.change(job, JobState.DONE, summary="Copied 2 files")
    jobs.dismiss(job)
    settle(qtbot)

    assert island.calls == []


def test_an_island_that_appears_gets_the_jobs_still_running(qtbot):
    jobs, island = FakeJobs(), FakeIsland(available=False)
    reporter = IslandReporter(jobs, island, interval_ms=INTERVAL_MS)
    running, queued, over = jobs.add(), jobs.add(), jobs.add()
    jobs.run(running, 0.5)
    jobs.change(over, JobState.DONE, summary="Copied 2 files")

    island.appear()

    assert [(method, call_id) for method, call_id, _ in island.calls] == [
        ("Show", f"job-{running.id}")
    ]
    assert island.calls[0][2]["progress"] == 0.5
    assert reporter is not None
    del queued


def test_an_island_that_restarts_is_told_everything_again(qtbot, setup):
    jobs, island, _ = setup
    job = jobs.add()
    jobs.run(job, 0.2)
    settle(qtbot)

    island.vanish()
    jobs.run(job, 0.6)
    settle(qtbot)
    assert island.methods() == ["Show"]

    island.appear()

    # Show, not Update: the new island has never heard of this activity
    assert island.methods() == ["Show", "Show"]
    assert island.calls[-1][2]["progress"] == 0.6
    assert island.calls[-1][2]["title"] == "Copying 2 items to Pictures"


def test_failures_from_before_a_restart_are_not_dismissed_in_the_new_island(qtbot, setup):
    jobs, island, _ = setup
    job = failed_job(jobs)
    island.vanish()
    island.appear()
    count = len(island.calls)

    jobs.dismiss(job)

    assert len(island.calls) == count


# Errors from the island


def test_not_found_stops_the_updates(qtbot, setup):
    jobs, island, _ = setup
    job = jobs.add()
    jobs.run(job, 0.1)
    island.replies = ["NotFound"]

    jobs.run(job, 0.2)
    settle(qtbot)
    count = len(island.calls)
    jobs.run(job, 0.3)
    jobs.change(job, JobState.DONE, summary="Copied 2 files")
    settle(qtbot)

    assert len(island.calls) == count


def test_a_rate_limited_final_state_is_sent_again(qtbot, setup, monkeypatch):
    from filehouse.integration import island as island_module

    monkeypatch.setattr(island_module, "_RETRY_MS", INTERVAL_MS)
    jobs, island, _ = setup
    job = jobs.add()
    jobs.run(job, 0.1)
    island.replies = ["RateLimited"]

    jobs.change(job, JobState.DONE, summary="Copied 2 files")
    assert island.calls[-1][2]["state"] == "done"
    count = len(island.calls)

    qtbot.waitUntil(lambda: len(island.calls) == count + 1, timeout=2000)
    assert island.calls[-1][2] == {
        "state": "done",
        "title": "Copied 2 files",
        "text": "",
        "progress": 1.0,
    }
    settle(qtbot)
    assert len(island.calls) == count + 1


def test_a_refused_show_is_tried_again(qtbot, setup, monkeypatch):
    from filehouse.integration import island as island_module

    monkeypatch.setattr(island_module, "_RETRY_MS", INTERVAL_MS)
    jobs, island, _ = setup
    job = jobs.add()
    island.replies = ["RateLimited"]

    jobs.run(job, 0.1)

    qtbot.waitUntil(lambda: island.methods() == ["Show", "Show"], timeout=2000)
    jobs.run(job, 0.7)
    settle(qtbot)
    assert island.state_of(f"job-{job.id}")["progress"] == 0.7


def test_a_job_keeps_going_when_the_island_keeps_refusing(qtbot, setup, monkeypatch):
    from filehouse.integration import island as island_module

    monkeypatch.setattr(island_module, "_RETRY_MS", 5)
    jobs, island, _ = setup
    job = jobs.add()
    island.replies = ["Failed"] * 50

    jobs.run(job, 0.1)
    qtbot.wait(200)

    # It gives up after a few tries instead of calling for ever
    assert 2 <= len(island.calls) <= 8


def test_when_the_island_is_full_the_oldest_failure_makes_room(qtbot, setup):
    jobs, island, _ = setup
    first, second = failed_job(jobs), failed_job(jobs)
    job = jobs.add()
    island.replies = ["LimitExceeded"]

    jobs.run(job, 0.1)

    assert island.calls[-3:] == [
        ("Show", f"job-{job.id}", island.calls[-1][2]),
        ("Dismiss", f"job-{first.id}", {}),
        ("Show", f"job-{job.id}", island.calls[-1][2]),
    ]
    # The failure is still in the app's own panel
    assert first in jobs.jobs and second in jobs.jobs


def test_changes_made_while_a_show_is_on_its_way_follow_it(qtbot, setup):
    jobs, island, _ = setup
    island.hold = True
    job = jobs.add()

    jobs.run(job, 0.1)
    jobs.run(job, 0.6)
    jobs.change(job, JobState.DONE, summary="Copied 2 files")
    assert island.methods() == ["Show"]

    island.hold = False
    island.release()

    assert island.methods() == ["Show", "Update"]
    assert island.state_of(f"job-{job.id}")["state"] == "done"
