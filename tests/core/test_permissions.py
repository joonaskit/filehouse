import os
import stat

import pytest
from gi.repository import Gio, GLib
from helpers import Recorder, gfile, make_tree

from filehouse.core.operations import Answer, Failure, JobKind, JobState, permissions
from filehouse.core.operations.permissions import PRESETS, PermissionsJob

BY_KEY = {preset.key: preset for preset in PRESETS}


def run(preset, paths, prompt=None, hook=None):
    """Run a permissions job to its end on this thread, with `.seen`, a Recorder."""
    seen = Recorder(hook)
    job = PermissionsJob(preset, [gfile(path) for path in paths], prompt=prompt, on_change=seen)
    job.seen = seen
    job.run()
    assert job.state.is_final and seen.states[-1] is job.state
    return job


def messages(job):
    return [str(error) for error in job.errors]


def mode(path):
    return stat.S_IMODE(os.lstat(path).st_mode)


def test_the_presets_are_the_four_of_the_design():
    assert [(p.label, oct(p.file_mode)[2:]) for p in PRESETS] == [
        ("Private", "600"),
        ("Shared", "664"),
        ("Make executable", "755"),
        ("Read-only", "444"),
    ]


@pytest.mark.parametrize("preset", PRESETS, ids=lambda preset: preset.key)
def test_a_file_gets_the_mode_of_the_preset(tmp_path, preset):
    path = tmp_path / "file"
    path.write_text("content")
    path.chmod(0o640)

    job = run(preset, [path])

    assert job.kind is JobKind.PERMISSIONS
    assert job.state is JobState.DONE and job.changed == 1
    assert job.summary == "Changed the permissions of 1 item"
    assert mode(path) == preset.file_mode
    assert path.read_text() == "content"


@pytest.mark.parametrize(
    "key, expected", [("private", 0o700), ("shared", 0o775), ("read-only", 0o555)]
)
def test_a_folder_gets_the_open_bit_with_every_read_bit(tmp_path, key, expected):
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "inside").write_text("")
    (folder / "inside").chmod(0o640)
    try:
        job = run(BY_KEY[key], [folder])

        assert job.state is JobState.DONE and job.changed == 1
        assert mode(folder) == expected
        # It can still be opened, and what is in it was not touched
        assert os.listdir(folder) == ["inside"]
        assert mode(folder / "inside") == 0o640
    finally:
        folder.chmod(0o700)


def test_make_executable_leaves_folders_alone(tmp_path):
    make_tree(tmp_path, {"folder": {}, "script.sh": "#!/bin/sh"})
    (tmp_path / "folder").chmod(0o700)
    (tmp_path / "script.sh").chmod(0o600)

    job = run(BY_KEY["executable"], [tmp_path / "folder", tmp_path / "script.sh"])

    assert job.state is JobState.DONE
    assert (job.changed, job.left_alone) == (1, 1)
    assert job.summary == "Changed the permissions of 1 file, folders left as they were"
    assert mode(tmp_path / "folder") == 0o700
    assert mode(tmp_path / "script.sh") == 0o755


def test_a_link_is_never_followed_and_is_reported_without_asking(tmp_path):
    make_tree(
        tmp_path,
        {
            "target": "t",
            "folder": {},
            "link": ("link", "target"),
            "folder link": ("link", "folder"),
            "broken": ("link", "nowhere"),
        },
    )
    (tmp_path / "target").chmod(0o640)
    (tmp_path / "folder").chmod(0o750)

    asked = []
    job = run(
        BY_KEY["private"],
        [tmp_path / name for name in ("link", "folder link", "broken")],
        prompt=asked.append,
    )

    # Asking again would not help: there is nothing to retry
    assert asked == [] and not job.can_retry
    assert job.state is JobState.FAILED
    assert job.changed == 0 and job.left_alone == 0
    assert job.summary == "Changed the permissions of 0 of 3 items, 3 failed"
    assert messages(job) == [
        "link: a link has no permissions of its own",
        "folder link: a link has no permissions of its own",
        "broken: a link has no permissions of its own",
    ]
    assert all(error.step == "change the permissions of" for error in job.errors)
    # What they point to is as it was
    assert mode(tmp_path / "target") == 0o640
    assert mode(tmp_path / "folder") == 0o750


def test_a_missing_file_is_named_and_the_rest_is_still_done(tmp_path):
    make_tree(tmp_path, {"a": "a", "c": "c"})
    for name in ("a", "c"):
        (tmp_path / name).chmod(0o640)

    job = run(BY_KEY["read-only"], [tmp_path / name for name in ("a", "gone", "c")])

    assert job.state is JobState.FAILED and job.changed == 2
    assert messages(job) == ["gone: no such file or folder"]
    assert job.errors[0].path == str(tmp_path / "gone")
    assert job.summary == "Changed the permissions of 2 of 3 items, 1 failed"
    assert mode(tmp_path / "a") == mode(tmp_path / "c") == 0o444


@pytest.fixture
def theirs(tmp_path, monkeypatch):
    """Three files, of which "theirs" may not be changed while `refused` is set: as for a
    file that belongs to someone else, which only its owner may change."""
    make_tree(tmp_path, {"mine": "", "theirs": "", "mine too": ""})
    for name in ("mine", "theirs", "mine too"):
        (tmp_path / name).chmod(0o640)
    set_mode = permissions._set_mode
    refused = {"theirs"}

    def refuse_theirs(location, new_mode, cancellable):
        if location.get_basename() in refused:
            raise GLib.Error.new_literal(
                Gio.io_error_quark(), "Operation not permitted", Gio.IOErrorEnum.PERMISSION_DENIED
            )
        set_mode(location, new_mode, cancellable)

    monkeypatch.setattr(permissions, "_set_mode", refuse_theirs)
    return [tmp_path / name for name in ("mine", "theirs", "mine too")], refused


def test_a_file_that_may_not_be_changed_is_named_and_the_rest_is_still_done(tmp_path, theirs):
    paths, _refused = theirs

    job = run(BY_KEY["shared"], paths)

    # Without a prompt it is skipped
    assert job.state is JobState.FAILED and job.changed == 2
    assert messages(job) == ["theirs: permission denied"]
    assert mode(tmp_path / "theirs") == 0o640
    assert mode(tmp_path / "mine") == mode(tmp_path / "mine too") == 0o664


def test_a_folder_keeps_set_group_id_and_sticky_and_a_file_loses_its_special_bits(tmp_path):
    folder = tmp_path / "shared folder"
    folder.mkdir()
    folder.chmod(0o3770)
    program = tmp_path / "program"
    program.write_text("")
    program.chmod(0o4755)
    assert mode(folder) == 0o3770 and mode(program) == 0o4755

    run(BY_KEY["shared"], [folder, program])

    assert mode(folder) == 0o3775
    assert mode(program) == 0o664


def test_a_failure_is_asked_about_and_retry_tries_it_again(tmp_path, theirs):
    paths, refused = theirs
    questions = []

    def prompt(question):
        questions.append(question)
        # It may be changed now, as after asking its owner
        refused.clear()
        return Answer.RETRY

    job = run(BY_KEY["shared"], paths, prompt=prompt)

    assert [type(question) for question in questions] == [Failure]
    assert str(questions[0].error) == "theirs: permission denied"
    assert job.state is JobState.DONE and job.errors == []
    assert job.summary == "Changed the permissions of 3 items"
    assert mode(tmp_path / "theirs") == 0o664


def test_a_skipped_failure_can_be_retried_as_a_job_of_its_own(tmp_path, theirs):
    paths, refused = theirs

    job = run(BY_KEY["shared"], paths, prompt=lambda _question: Answer.SKIP)

    assert job.state is JobState.FAILED and job.can_retry
    assert job.summary == "Changed the permissions of 2 of 3 items, 1 failed"
    refused.clear()
    again = job.retry()
    assert [location.get_basename() for location in again.sources] == ["theirs"]
    again.run()
    assert again.state is JobState.DONE
    assert mode(tmp_path / "theirs") == 0o664


def test_cancel_from_the_question_stops_and_says_how_far_it_got(tmp_path, theirs):
    paths, _refused = theirs

    job = run(BY_KEY["shared"], paths, prompt=lambda _question: Answer.CANCEL)

    assert job.state is JobState.CANCELLED
    assert job.summary == "Cancelled after 1 of 3 items"
    # What came after was not changed
    assert mode(tmp_path / "mine") == 0o664
    assert mode(tmp_path / "mine too") == 0o640


def test_cancelled_while_running_it_stops_between_items(tmp_path):
    make_tree(tmp_path, {name: "" for name in "abcd"})
    for name in "abcd":
        (tmp_path / name).chmod(0o640)

    def cancel_after_two(job):
        if job.progress.files_done == 2:
            job.cancel()

    job = run(BY_KEY["private"], [tmp_path / name for name in "abcd"], hook=cancel_after_two)

    assert job.state is JobState.CANCELLED
    assert JobState.DONE not in job.seen.states
    assert job.summary == "Cancelled after 2 of 4 items"
    assert [mode(tmp_path / name) for name in "abcd"] == [0o600, 0o600, 0o640, 0o640]


def test_progress_counts_the_items_and_done_comes_only_at_the_end(tmp_path):
    make_tree(tmp_path, {name: "" for name in "abc"})

    job = run(BY_KEY["private"], [tmp_path / name for name in "abc"])

    assert job.seen.states == [JobState.PLANNING, JobState.RUNNING, JobState.DONE]
    done = [progress.files_done for progress in job.seen.progress if progress.planned]
    assert done == sorted(done) and done[-1] == 3
    assert all(progress.files_total == 3 for progress in job.seen.progress if progress.planned)


def test_cancelled_before_it_starts_changes_nothing(tmp_path):
    path = tmp_path / "file"
    path.write_text("")
    path.chmod(0o640)
    job = PermissionsJob(BY_KEY["private"], [gfile(path)])

    job.cancel()
    job.run()

    assert job.state is JobState.CANCELLED and not job.started
    assert mode(path) == 0o640


def test_nothing_selected_is_done_at_once():
    job = run(BY_KEY["private"], [])

    assert job.state is JobState.DONE
