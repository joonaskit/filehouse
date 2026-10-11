import pytest
from helpers import gfile, leftovers, make_tree, never_done, read_tree, run_job

from filehouse.core.operations import Answer, Conflict, Failure, JobKind, JobState, Reply
from filehouse.core.operations.conflicts import free_name, split_extension


class Answers:
    """A prompt that gives the same answer to every conflict and remembers the questions.
    A failed item is skipped."""

    def __init__(self, answer, apply_to_all=False):
        self.reply = Reply(answer, apply_to_all)
        self.questions = []

    def __call__(self, question):
        if isinstance(question, Failure):
            return Answer.SKIP
        assert isinstance(question, Conflict)
        self.questions.append(question)
        return self.reply

    @property
    def names(self):
        return [question.source.get_basename() for question in self.questions]


def test_without_a_prompt_nothing_is_replaced(tmp_path):
    src = make_tree(tmp_path / "src", {"a.txt": "new", "b.txt": "b"})
    dst = make_tree(tmp_path / "dst", {"a.txt": "old"})

    job = run_job(JobKind.COPY, [src / "a.txt", src / "b.txt"], dst)

    assert job.state is JobState.FAILED
    assert str(job.error) == "a.txt: already exists at the destination"
    assert job.summary == "Copied 1 of 2 files, 1 skipped"
    assert read_tree(dst) == {"a.txt": "old", "b.txt": "b"}


def test_skip_leaves_the_destination_and_the_job_is_still_done(tmp_path):
    src = make_tree(tmp_path / "src", {"a.txt": "new", "b.txt": "b"})
    dst = make_tree(tmp_path / "dst", {"a.txt": "old"})
    prompt = Answers(Answer.SKIP)

    job = run_job(JobKind.COPY, [src / "a.txt", src / "b.txt"], dst, prompt=prompt)

    assert job.state is JobState.DONE
    assert job.summary == "Copied 1 file"
    assert job.progress.fraction == 1.0
    assert read_tree(dst) == {"a.txt": "old", "b.txt": "b"}
    assert prompt.names == ["a.txt"]


def test_replace_overwrites_the_file(tmp_path):
    src = make_tree(tmp_path / "src", {"a.txt": "new"})
    dst = make_tree(tmp_path / "dst", {"a.txt": "old"})
    prompt = Answers(Answer.REPLACE)

    job = run_job(JobKind.COPY, [src / "a.txt"], dst, prompt=prompt)

    assert job.state is JobState.DONE
    assert read_tree(dst) == {"a.txt": "new"}
    assert prompt.questions[0].can_replace
    assert prompt.questions[0].destination.get_path() == str(dst / "a.txt")
    assert not leftovers(tmp_path)


def test_keep_both_picks_a_free_name(tmp_path):
    src = make_tree(tmp_path / "src", {"a.txt": "new", "sub": {"f": "new"}})
    dst = make_tree(tmp_path / "dst", {"a.txt": "old", "a (2).txt": "older", "sub": {"f": "old"}})

    job = run_job(JobKind.COPY, [src / "a.txt", src / "sub"], dst, prompt=Answers(Answer.KEEP_BOTH))

    assert job.state is JobState.DONE
    assert read_tree(dst) == {
        "a.txt": "old",
        "a (2).txt": "older",
        "a (3).txt": "new",
        "sub": {"f": "old"},
        "sub (2)": {"f": "new"},
    }


def test_free_names_go_before_the_extension(tmp_path):
    for name in ("archive.tar.gz", ".bashrc", "notes", "v1.2"):
        (tmp_path / name).write_text("")

    def free(name, is_dir=False):
        return free_name(gfile(tmp_path), name, is_dir)

    assert free("archive.tar.gz") == "archive (2).tar.gz"
    assert free(".bashrc") == ".bashrc (2)"
    assert free("notes") == "notes (2)"
    assert free("v1.2", is_dir=True) == "v1.2 (2)"
    assert split_extension("report.pdf") == ("report", ".pdf")
    assert split_extension(".tar.gz") == (".tar", ".gz")


def test_apply_to_all_asks_once(tmp_path):
    names = ["a.txt", "b.txt", "c.txt"]
    src = make_tree(tmp_path / "src", dict.fromkeys(names, "new"))
    dst = make_tree(tmp_path / "dst", dict.fromkeys(names, "old"))
    prompt = Answers(Answer.REPLACE, apply_to_all=True)

    job = run_job(JobKind.COPY, [src / name for name in names], dst, prompt=prompt)

    assert job.state is JobState.DONE
    assert prompt.names == ["a.txt"]
    assert read_tree(dst) == dict.fromkeys(names, "new")


def test_replace_on_a_folder_merges(tmp_path):
    src = make_tree(
        tmp_path / "src", {"album": {"new.jpg": "n", "both.jpg": "new", "in": {"x": "x"}}}
    )
    dst = make_tree(
        tmp_path / "dst", {"album": {"old.jpg": "o", "both.jpg": "old", "in": {"y": "y"}}}
    )
    replies = {"album": Answer.REPLACE, "both.jpg": Answer.SKIP}
    asked = []

    def prompt(question):
        asked.append(question.source.get_basename())
        return replies[asked[-1]]

    job = run_job(JobKind.COPY, [src / "album"], dst, prompt=prompt)

    assert job.state is JobState.DONE
    # The folder and the folders inside it merge; a file inside is its own question
    assert asked == ["album", "both.jpg"]
    assert read_tree(dst) == {
        "album": {"old.jpg": "o", "both.jpg": "old", "new.jpg": "n", "in": {"x": "x", "y": "y"}}
    }


def test_skipping_a_folder_skips_what_is_in_it(tmp_path):
    src = make_tree(tmp_path / "src", {"album": {"both.jpg": "new", "new.jpg": "n"}, "z": "z"})
    dst = make_tree(tmp_path / "dst", {"album": {"both.jpg": "old"}})
    prompt = Answers(Answer.SKIP)

    job = run_job(JobKind.COPY, [src / "album", src / "z"], dst, prompt=prompt)

    assert job.state is JobState.DONE
    assert job.summary == "Copied 1 file"
    assert prompt.names == ["album"]
    assert read_tree(dst) == {"album": {"both.jpg": "old"}, "z": "z"}


def test_conflicts_are_asked_before_anything_is_copied(tmp_path):
    src = make_tree(tmp_path / "src", {"a.txt": "a", "m.txt": "new", "z.txt": "new"})
    dst = make_tree(tmp_path / "dst", {"m.txt": "old", "z.txt": "old"})
    copied_when_asked = []

    def prompt(question):
        copied_when_asked.append((dst / "a.txt").exists())
        return Answer.REPLACE

    job = run_job(JobKind.COPY, [src / "a.txt", src / "m.txt", src / "z.txt"], dst, prompt=prompt)

    assert job.state is JobState.DONE
    assert copied_when_asked == [False, False]


@pytest.mark.parametrize("kind", [JobKind.COPY, JobKind.MOVE])
def test_a_file_never_replaces_a_folder_or_the_other_way_round(tmp_path, kind):
    src = make_tree(tmp_path / "src", {"thing": "a file", "other": {"inside": "x"}})
    before = {"thing": {"inside": "y"}, "other": "a file"}
    dst = make_tree(tmp_path / "dst", before)
    prompt = Answers(Answer.REPLACE, apply_to_all=True)

    job = run_job(kind, [src / "other", src / "thing"], dst, prompt=prompt)

    assert job.state is JobState.FAILED
    assert never_done(job)
    assert [question.can_replace for question in prompt.questions] == [False, False]
    assert Answer.REPLACE not in prompt.questions[0].answers
    assert Answer.KEEP_BOTH in prompt.questions[0].answers
    assert [str(error) for error in job.errors] == [
        "other: cannot replace a file with a folder",
        "thing: cannot replace a folder with a file",
    ]
    assert job.errors[1].path == str(src / "thing")
    assert read_tree(dst) == before
    assert read_tree(src) == {"thing": "a file", "other": {"inside": "x"}}


def test_keep_both_works_when_the_kinds_differ(tmp_path):
    src = make_tree(tmp_path / "src", {"thing": "a file"})
    dst = make_tree(tmp_path / "dst", {"thing": {"inside": "y"}})

    job = run_job(JobKind.COPY, [src / "thing"], dst, prompt=Answers(Answer.KEEP_BOTH))

    assert job.state is JobState.DONE
    assert read_tree(dst) == {"thing": {"inside": "y"}, "thing (2)": "a file"}


def test_replace_for_all_still_asks_when_the_kinds_differ(tmp_path):
    src = make_tree(tmp_path / "src", {"a.txt": "new", "thing": "a file"})
    dst = make_tree(tmp_path / "dst", {"a.txt": "old", "thing": {}})
    asked = []

    def prompt(question):
        asked.append(question.source.get_basename())
        if question.can_replace:
            return Reply(Answer.REPLACE, apply_to_all=True)
        return Answer.SKIP

    job = run_job(JobKind.COPY, [src / "a.txt", src / "thing"], dst, prompt=prompt)

    assert job.state is JobState.DONE
    assert asked == ["a.txt", "thing"]
    assert read_tree(dst) == {"a.txt": "new", "thing": {}}


def test_a_folder_is_never_written_through_a_link(tmp_path):
    src = make_tree(tmp_path / "src", {"album": {"new.jpg": "n"}})
    elsewhere = make_tree(tmp_path / "elsewhere", {})
    dst = make_tree(tmp_path / "dst", {"album": ("link", elsewhere)})
    prompt = Answers(Answer.REPLACE)

    job = run_job(JobKind.COPY, [src / "album"], dst, prompt=prompt)

    assert job.state is JobState.FAILED
    assert not prompt.questions[0].can_replace
    assert read_tree(elsewhere) == {}


def test_a_file_that_appears_after_planning_is_still_a_conflict(tmp_path):
    src = make_tree(tmp_path / "src", {"a.txt": "new"})
    dst = make_tree(tmp_path / "dst", {})
    prompt = Answers(Answer.SKIP)

    def appear(job):
        if job.state is JobState.RUNNING and not (dst / "a.txt").exists():
            (dst / "a.txt").write_text("came from elsewhere")

    job = run_job(JobKind.COPY, [src / "a.txt"], dst, prompt=prompt, hook=appear)

    assert prompt.names == ["a.txt"]
    assert job.state is JobState.DONE
    assert read_tree(dst) == {"a.txt": "came from elsewhere"}
    # It was noticed before any of the file was copied
    assert all(progress.bytes_done == 0 for progress in job.seen.progress)


def test_a_file_that_appears_during_the_copy_is_not_overwritten(tmp_path, slow_copy):
    src = make_tree(tmp_path / "src", {"a.txt": "new" * 2000})
    dst = make_tree(tmp_path / "dst", {})
    prompt = Answers(Answer.SKIP)

    def appear(job):
        if job.progress.bytes_done > 2000 and not (dst / "a.txt").exists():
            (dst / "a.txt").write_text("came from elsewhere")

    job = run_job(JobKind.COPY, [src / "a.txt"], dst, prompt=prompt, hook=appear)

    assert prompt.names == ["a.txt"]
    assert job.state is JobState.DONE
    assert read_tree(dst) == {"a.txt": "came from elsewhere"}
    assert not leftovers(tmp_path)


def test_two_sources_with_one_name_conflict_with_each_other(tmp_path):
    make_tree(tmp_path, {"one": {"a.txt": "first"}, "two": {"a.txt": "second"}, "dst": {}})
    prompt = Answers(Answer.KEEP_BOTH)

    job = run_job(
        JobKind.COPY,
        [tmp_path / "one" / "a.txt", tmp_path / "two" / "a.txt"],
        tmp_path / "dst",
        prompt,
    )

    assert job.state is JobState.DONE
    assert read_tree(tmp_path / "dst") == {"a.txt": "first", "a (2).txt": "second"}


def test_copying_a_file_onto_itself_can_only_keep_both(tmp_path):
    src = make_tree(tmp_path / "src", {"a.txt": "a"})
    prompt = Answers(Answer.KEEP_BOTH)

    job = run_job(JobKind.COPY, [src / "a.txt"], src, prompt=prompt)

    assert job.state is JobState.DONE
    assert not prompt.questions[0].can_replace
    assert read_tree(src) == {"a.txt": "a", "a (2).txt": "a"}


def test_replacing_a_file_with_itself_fails_and_keeps_it(tmp_path):
    src = make_tree(tmp_path / "src", {"a.txt": "a"})

    job = run_job(JobKind.MOVE, [src / "a.txt"], src, prompt=Answers(Answer.REPLACE))

    assert job.state is JobState.FAILED
    assert read_tree(src) == {"a.txt": "a"}


@pytest.mark.parametrize("cross_device", [False, True])
def test_move_with_conflicts(tmp_path, monkeypatch, cross_device):
    if cross_device:
        from helpers import not_supported

        from filehouse.core.operations import copy

        monkeypatch.setattr(copy, "_rename_item", not_supported)
    src = make_tree(
        tmp_path / "src", {"docs": {"keep.txt": "new", "swap.txt": "new", "add.txt": "+"}}
    )
    dst = make_tree(tmp_path / "dst", {"docs": {"keep.txt": "old", "swap.txt": "old"}})
    replies = {"docs": Answer.REPLACE, "keep.txt": Answer.SKIP, "swap.txt": Answer.REPLACE}

    job = run_job(
        JobKind.MOVE, [src / "docs"], dst, prompt=lambda q: replies[q.source.get_basename()]
    )

    assert job.state is JobState.DONE
    assert job.summary == "Moved 2 files"
    assert read_tree(dst) == {"docs": {"keep.txt": "old", "swap.txt": "new", "add.txt": "+"}}
    # The skipped file stays where it was, and so does the folder that holds it
    assert read_tree(src) == {"docs": {"keep.txt": "new"}}
    assert not leftovers(tmp_path)
