import os

import pytest
from helpers import make_tree, never_done, read_tree, run_job

from filehouse.core.errors import OperationError
from filehouse.core.operations import Answer, Failure, JobKind, JobState, Reply

pytestmark = pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permissions")


@pytest.fixture
def locked(tmp_path):
    """A source folder of three files, the middle one unreadable."""
    src = make_tree(tmp_path / "src", {"a.txt": "a", "locked.txt": "secret", "z.txt": "z"})
    (src / "locked.txt").chmod(0)
    yield src
    (src / "locked.txt").chmod(0o600)


def test_a_permission_error_names_the_file(tmp_path, locked):
    dst = make_tree(tmp_path / "dst", {})

    job = run_job(JobKind.COPY, [locked], dst)

    assert job.state is JobState.FAILED
    error = job.error
    assert isinstance(error, OperationError)
    assert error.path == str(locked / "locked.txt")
    assert error.step == "copy"
    assert error.message == "permission denied"
    assert str(error) == "locked.txt: permission denied"
    assert read_tree(dst) == {"src": {"a.txt": "a", "z.txt": "z"}}


def test_a_job_with_a_skipped_error_is_never_done(tmp_path, locked):
    job = run_job(JobKind.COPY, [locked], make_tree(tmp_path / "dst", {}))

    assert never_done(job)
    assert job.state is JobState.FAILED
    assert job.summary == "Copied 2 of 3 files, 1 skipped"
    assert job.progress.fraction < 1.0


def test_full_progress_is_not_done(tmp_path):
    # Every byte and every file is accounted for, and the job has still failed
    src = make_tree(tmp_path / "src", {"a.txt": "a", "sub": {}})
    (src / "sub").chmod(0o311)
    dst = make_tree(tmp_path / "dst", {})

    job = run_job(JobKind.COPY, [src / "a.txt", src / "sub"], dst)
    (src / "sub").chmod(0o755)

    assert job.progress.fraction == 1.0
    assert job.state is JobState.FAILED
    assert never_done(job)
    assert str(job.error) == "sub: permission denied"
    assert job.summary == "Copied 1 of 1 file, 1 failed"


def test_the_user_is_asked_and_retry_runs_the_step_again(tmp_path, locked):
    dst = make_tree(tmp_path / "dst", {})
    asked = []

    def prompt(question):
        assert isinstance(question, Failure)
        asked.append(str(question.error))
        (locked / "locked.txt").chmod(0o600)
        return Answer.RETRY

    job = run_job(JobKind.COPY, [locked], dst, prompt=prompt)

    assert asked == ["locked.txt: permission denied"]
    assert JobState.WAITING in job.seen.states
    assert job.state is JobState.DONE
    assert job.summary == "Copied 3 files"
    assert read_tree(dst)["src"]["locked.txt"] == "secret"


def test_skip_all_asks_once(tmp_path):
    names = ["1.txt", "2.txt", "3.txt"]
    src = make_tree(tmp_path / "src", dict.fromkeys(names, "x"))
    for name in names[:2]:
        (src / name).chmod(0)
    asked = []

    def prompt(question):
        asked.append(question.error.name)
        return Reply(Answer.SKIP, apply_to_all=True)

    job = run_job(JobKind.COPY, [src], make_tree(tmp_path / "dst", {}), prompt=prompt)

    assert asked == ["1.txt"]
    assert job.state is JobState.FAILED
    assert [error.name for error in job.errors] == ["1.txt", "2.txt"]
    assert job.summary == "Copied 1 of 3 files, 2 skipped"


def test_cancel_as_the_answer_to_an_error(tmp_path, locked):
    dst = make_tree(tmp_path / "dst", {})

    job = run_job(JobKind.COPY, [locked], dst, prompt=lambda question: Answer.CANCEL)

    assert job.state is JobState.CANCELLED
    assert job.summary == "Cancelled after 1 of 3 files"
    assert read_tree(dst) == {"src": {"a.txt": "a"}}


def test_a_folder_that_cannot_be_read_names_the_folder(tmp_path):
    src = make_tree(tmp_path / "src", {"a.txt": "a", "private": {"x": "x"}})
    (src / "private").chmod(0)
    dst = make_tree(tmp_path / "dst", {})
    asked = []

    def prompt(question):
        asked.append(str(question.error))
        if len(asked) == 1:
            return Answer.RETRY
        (src / "private").chmod(0o755)
        return Answer.RETRY

    try:
        job = run_job(JobKind.COPY, [src], dst, prompt=prompt)
    finally:
        (src / "private").chmod(0o755)

    assert asked == ["private: permission denied"] * 2
    assert job.state is JobState.DONE
    assert read_tree(dst) == {"src": {"a.txt": "a", "private": {"x": "x"}}}


def test_a_destination_that_cannot_be_written_fails_every_item(tmp_path):
    src = make_tree(tmp_path / "src", {"a.txt": "a", "sub": {"b.txt": "b"}})
    dst = make_tree(tmp_path / "dst", {})
    dst.chmod(0o555)

    try:
        job = run_job(JobKind.COPY, [src / "a.txt", src / "sub"], dst)
    finally:
        dst.chmod(0o755)

    assert job.state is JobState.FAILED
    assert [str(error) for error in job.errors] == [
        "a.txt: permission denied",
        "sub: permission denied",
    ]
    assert job.errors[1].step == "create folder"
    assert job.summary == "Copied 0 of 2 files, 2 skipped"
    assert read_tree(dst) == {}


def test_retry_makes_a_job_for_the_failed_items_only(tmp_path, locked):
    dst = make_tree(tmp_path / "dst", {})
    job = run_job(JobKind.COPY, [locked], dst)
    assert job.state is JobState.FAILED
    (locked / "locked.txt").chmod(0o600)

    again = job.retry()
    again.run()

    assert [source.get_basename() for source in again.sources] == ["locked.txt"]
    assert again.state is JobState.DONE
    assert again.summary == "Copied 1 file"
    assert read_tree(dst) == {"src": {"a.txt": "a", "locked.txt": "secret", "z.txt": "z"}}


def test_only_a_failed_job_can_be_retried(tmp_path):
    src = make_tree(tmp_path / "src", {"a.txt": "a"})

    job = run_job(JobKind.COPY, [src / "a.txt"], make_tree(tmp_path / "dst", {}))

    assert job.state is JobState.DONE
    assert job.retry() is None


def test_a_prompt_that_breaks_fails_the_job_instead_of_hanging_it(tmp_path, locked):
    def prompt(question):
        raise RuntimeError("the dialog fell over")

    job = run_job(JobKind.COPY, [locked], make_tree(tmp_path / "dst", {}), prompt=prompt)

    assert job.state is JobState.FAILED
    assert "the dialog fell over" in str(job.error)
