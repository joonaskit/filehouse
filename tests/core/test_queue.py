import threading

from helpers import gfile, make_tree, read_tree

from filehouse.core.operations import Job, JobKind, JobQueue, JobState


def in_thread(function):
    threading.Thread(target=function, daemon=True).start()


class Blocker:
    """Stands in for a job: it runs until it is released."""

    def __init__(self):
        self.state = JobState.QUEUED
        self.started = threading.Event()
        self.release = threading.Event()

    def run(self):
        self.state = JobState.RUNNING
        self.started.set()
        assert self.release.wait(5)
        self.state = JobState.DONE


def test_at_most_two_jobs_run_at_once():
    queue = JobQueue(in_thread)
    first, second, third = jobs = [Blocker(), Blocker(), Blocker()]
    for job in jobs:
        queue.add(job)

    assert first.started.wait(5) and second.started.wait(5)
    assert not third.started.wait(0.1)
    assert third.state is JobState.QUEUED

    first.release.set()
    assert third.started.wait(5)
    assert not queue.is_idle()

    second.release.set()
    third.release.set()
    for _ in range(100):
        if queue.is_idle():
            break
        threading.Event().wait(0.01)
    assert queue.is_idle()


def test_real_jobs_run_in_order_and_one_failing_does_not_stop_the_rest(tmp_path):
    src = make_tree(tmp_path / "src", {"a": "a", "b": "b"})
    dst = make_tree(tmp_path / "dst", {})
    queue = JobQueue(lambda function: function(), limit=1)
    jobs = [
        Job(JobKind.COPY, [gfile(src / "a")], gfile(dst)),
        Job(JobKind.COPY, [gfile(src / "missing")], gfile(dst)),
        Job(JobKind.COPY, [gfile(src / "b")], gfile(dst)),
    ]

    for job in jobs:
        queue.add(job)

    assert [job.state for job in jobs] == [JobState.DONE, JobState.FAILED, JobState.DONE]
    assert read_tree(dst) == {"a": "a", "b": "b"}
    assert queue.is_idle()


def test_a_job_cancelled_while_it_waits_is_not_started(tmp_path):
    src = make_tree(tmp_path / "src", {"a": "a"})
    dst = make_tree(tmp_path / "dst", {})
    queue = JobQueue(in_thread, limit=1)
    blocker = Blocker()
    waiting = Job(JobKind.COPY, [gfile(src / "a")], gfile(dst))
    queue.add(blocker)
    queue.add(waiting)
    assert blocker.started.wait(5)

    waiting.cancel()
    assert waiting.state is JobState.CANCELLED
    blocker.release.set()
    for _ in range(100):
        if queue.is_idle():
            break
        threading.Event().wait(0.01)

    assert queue.is_idle()
    assert waiting.state is JobState.CANCELLED
    assert read_tree(dst) == {}


def test_job_ids_are_unique(tmp_path):
    jobs = [Job(JobKind.TRASH, []) for _ in range(3)]

    assert len({job.id for job in jobs}) == 3
