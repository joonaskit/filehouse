import threading

from helpers import gfile, make_tree, read_tree

from filehouse.core.operations import Answer, Job, JobKind, JobQueue, JobState


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


class Asking:
    """A prompt that is answered only when the test says so, as a user away from the
    screen would. `asked` is set once a question has come."""

    def __init__(self, answer):
        self.answer = answer
        self.asked = threading.Event()
        self.answered = threading.Event()

    def __call__(self, _question):
        self.asked.set()
        assert self.answered.wait(5)
        return self.answer


def wait_until(condition):
    for _ in range(500):
        if condition():
            return True
        threading.Event().wait(0.01)
    return False


def asking_copy(tmp_path, name):
    """A copy of `name` onto a file of that name: it asks what to do about it."""
    src = make_tree(tmp_path / f"src-{name}", {name: "new"})
    dst = make_tree(tmp_path / f"dst-{name}", {name: "old"})
    prompt = Asking(Answer.SKIP)
    return Job(JobKind.COPY, [gfile(src / name)], gfile(dst), prompt=prompt), prompt


def test_a_job_waiting_for_an_answer_gives_its_place_to_the_next(tmp_path):
    queue = JobQueue(in_thread)
    first, first_prompt = asking_copy(tmp_path, "a")
    second, second_prompt = asking_copy(tmp_path, "b")
    trash_soon = Blocker()
    for job in (first, second, trash_soon):
        queue.add(job)

    # Two questions nobody has answered yet: the third job starts all the same
    assert first_prompt.asked.wait(5) and second_prompt.asked.wait(5)
    assert first.state is JobState.WAITING and second.state is JobState.WAITING
    assert trash_soon.started.wait(5)
    assert not queue.is_idle()

    trash_soon.release.set()
    first_prompt.answered.set()
    second_prompt.answered.set()
    assert wait_until(queue.is_idle)
    assert first.state is JobState.DONE and second.state is JobState.DONE


def test_an_answered_job_goes_on_at_once_even_with_both_places_taken(tmp_path):
    queue = JobQueue(in_thread)
    answered, prompt = asking_copy(tmp_path, "a")
    others = [Blocker(), Blocker()]
    queue.add(answered)
    assert prompt.asked.wait(5)
    for job in others:
        queue.add(job)
    assert all(job.started.wait(5) for job in others)

    prompt.answered.set()

    # Not "Waiting to start" again: it finishes while the other two still run
    assert wait_until(lambda: answered.state is JobState.DONE)
    assert all(job.state is JobState.RUNNING for job in others)
    # And the places it took back are not given away twice
    late = Blocker()
    queue.add(late)
    assert not late.started.wait(0.2)
    others[0].release.set()
    assert late.started.wait(5)

    others[1].release.set()
    late.release.set()
    assert wait_until(queue.is_idle)
