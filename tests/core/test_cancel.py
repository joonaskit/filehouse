import threading

from helpers import Recorder, gfile, leftovers, make_tree, read_tree, run_job

from filehouse.core.operations import Answer, Job, JobKind, JobState


def cancel_inside_file(number, at_least):
    """A hook that cancels once the file after `number` finished ones has `at_least` bytes."""
    start = {}

    def hook(job):
        progress = job.progress
        if progress.files_done == number:
            start.setdefault("bytes", progress.bytes_done)
            if progress.bytes_done - start["bytes"] >= at_least:
                job.cancel()

    return hook


def test_cancel_mid_file_removes_only_the_partial_file(tmp_path, slow_copy):
    src = make_tree(tmp_path / "src", {"1.txt": "a" * 4000, "2.txt": "b" * 4000, "3.txt": "c"})
    dst = make_tree(tmp_path / "dst", {"mine.txt": "keep me"})

    job = run_job(
        JobKind.COPY,
        [src / "1.txt", src / "2.txt", src / "3.txt"],
        dst,
        hook=cancel_inside_file(1, 2000),
    )

    assert job.state is JobState.CANCELLED
    assert job.summary == "Cancelled after 1 of 3 files"
    # The finished file stays, the partial one is gone, and nothing else was touched
    assert read_tree(dst) == {"mine.txt": "keep me", "1.txt": "a" * 4000}
    assert not leftovers(tmp_path)
    assert len(read_tree(src)) == 3


def test_cancel_mid_file_on_a_real_copy(tmp_path, real_other_device):
    # Large enough that Gio reports progress, and checks the cancellable, between chunks
    src = tmp_path / "big.bin"
    src.write_bytes(b"\0" * (16 << 20))

    job = run_job(
        JobKind.COPY,
        [src],
        real_other_device,
        hook=lambda job: 0 < job.progress.bytes_done < (16 << 20) and job.cancel(),
    )

    assert job.state is JobState.CANCELLED
    assert job.summary == "Cancelled after 0 of 1 file"
    assert list(real_other_device.iterdir()) == []


def test_cancel_while_replacing_leaves_the_old_file_whole(tmp_path, slow_copy):
    src = make_tree(tmp_path / "src", {"report.txt": "new" * 2000})
    dst = make_tree(tmp_path / "dst", {"report.txt": "old contents"})

    job = run_job(
        JobKind.COPY,
        [src / "report.txt"],
        dst,
        prompt=lambda question: Answer.REPLACE,
        hook=cancel_inside_file(0, 2000),
    )

    assert job.state is JobState.CANCELLED
    assert read_tree(dst) == {"report.txt": "old contents"}


def test_cancelled_is_reported_only_after_the_work_stopped(tmp_path, slow_copy):
    src = make_tree(tmp_path / "src", {"1.txt": "a" * 4000})
    dst = make_tree(tmp_path / "dst", {})
    partials = []

    def hook(job):
        if job.state is JobState.CANCELLED:
            partials.append(list(dst.iterdir()))
        elif job.progress.bytes_done >= 2000:
            job.cancel()
            assert job.state is JobState.RUNNING

    job = run_job(JobKind.COPY, [src / "1.txt"], dst, hook=hook)

    assert job.state is JobState.CANCELLED
    assert partials == [[]]


def test_cancel_unblocks_a_question_nobody_answers(tmp_path):
    src = make_tree(tmp_path / "src", {"a.txt": "new"})
    dst = make_tree(tmp_path / "dst", {"a.txt": "old"})
    waiting = threading.Event()
    seen = Recorder(lambda job: job.state is JobState.WAITING and waiting.set())
    # Answering later is allowed: the prompt returns nothing and never answers
    job = Job(
        JobKind.COPY, [gfile(src / "a.txt")], gfile(dst), prompt=lambda q: None, on_change=seen
    )
    thread = threading.Thread(target=job.run)
    thread.start()

    assert waiting.wait(5)
    job.cancel()
    thread.join(5)

    assert not thread.is_alive()
    assert job.state is JobState.CANCELLED
    assert read_tree(dst) == {"a.txt": "old"}


def test_a_question_can_be_answered_later_from_another_thread(tmp_path):
    src = make_tree(tmp_path / "src", {"a.txt": "new"})
    dst = make_tree(tmp_path / "dst", {"a.txt": "old"})

    def prompt(question):
        threading.Timer(0.05, question.answer, [Answer.REPLACE]).start()

    job = run_job(JobKind.COPY, [src / "a.txt"], dst, prompt=prompt)

    assert job.state is JobState.DONE
    assert JobState.WAITING in job.seen.states
    assert read_tree(dst) == {"a.txt": "new"}


def test_a_job_cancelled_while_queued_never_runs(tmp_path):
    src = make_tree(tmp_path / "src", {"a.txt": "a"})
    dst = make_tree(tmp_path / "dst", {})
    seen = Recorder()
    job = Job(JobKind.COPY, [gfile(src / "a.txt")], gfile(dst), on_change=seen)

    job.cancel()
    job.run()

    assert seen.states == [JobState.CANCELLED]
    assert job.summary == "Cancelled before it started"
    assert read_tree(dst) == {}


def test_cancel_as_an_answer_stops_the_job(tmp_path):
    src = make_tree(tmp_path / "src", {"a.txt": "new", "b.txt": "b"})
    dst = make_tree(tmp_path / "dst", {"a.txt": "old"})

    job = run_job(JobKind.COPY, [src / "a.txt", src / "b.txt"], dst, prompt=lambda q: Answer.CANCEL)

    assert job.state is JobState.CANCELLED
    assert job.summary == "Cancelled after 0 of 2 files"
    assert read_tree(dst) == {"a.txt": "old"}
