"""Extracting and compressing. The archives that try to write where they should not
are made here, with the standard library, so the tests do not trust the code under test
to make them."""

import io
import os
import stat
import tarfile
import zipfile

import pytest
from helpers import Recorder, gfile, leftovers, make_tree, read_tree

from filehouse.core import archives
from filehouse.core.operations import Answer, JobState, NameTaken, Password, Reply
from filehouse.core.operations.archive import CompressJob, ExtractJob

pytestmark = pytest.mark.skipif(not archives.available(), reason="libarchive is not installed")

TREE = {"a.txt": "aaa", "sub": {"b.txt": "bb", "deep": {"c.txt": "c"}}}


def make_zip(path, files):
    """`files` maps the names in the archive to their text."""
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return path


def make_tar(path, members, mode="w:gz"):
    """`members` is a list of (name, what): text for a file, ("link", target),
    ("hardlink", target), ("fifo",) or None for a folder."""
    with tarfile.open(path, mode) as archive:
        for name, what in members:
            info = tarfile.TarInfo(name)
            info.mode = 0o644
            if what is None:
                info.type, info.mode = tarfile.DIRTYPE, 0o755
                archive.addfile(info)
            elif isinstance(what, tuple) and what[0] == "link":
                info.type, info.linkname = tarfile.SYMTYPE, what[1]
                archive.addfile(info)
            elif isinstance(what, tuple) and what[0] == "hardlink":
                info.type, info.linkname = tarfile.LNKTYPE, what[1]
                archive.addfile(info)
            elif isinstance(what, tuple) and what[0] == "fifo":
                info.type = tarfile.FIFOTYPE
                archive.addfile(info)
            else:
                data = what.encode() if isinstance(what, str) else what
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
    return path


def extract(archive, destination, prompt=None, hook=None):
    seen = Recorder(hook)
    job = ExtractJob(gfile(archive), gfile(destination), prompt=prompt, on_change=seen)
    job.seen = seen
    job.run()
    assert job.state.is_final and seen.states[-1] is job.state
    return job


def compress(sources, destination, name, format="zip", prompt=None, hook=None):
    seen = Recorder(hook)
    job = CompressJob(
        [gfile(source) for source in sources],
        gfile(destination),
        name,
        format,
        prompt=prompt,
        on_change=seen,
    )
    job.seen = seen
    job.run()
    assert job.state.is_final and seen.states[-1] is job.state
    return job


def answering(*replies):
    """A prompt that gives these replies in turn and remembers what it was asked."""
    asked, waiting = [], list(replies)

    def prompt(question):
        asked.append(question)
        return waiting.pop(0)

    prompt.asked = asked
    return prompt


# What a path from an archive may be


@pytest.mark.parametrize(
    "name, parts",
    [
        ("a.txt", ("a.txt",)),
        ("./a//b/", ("a", "b")),
        ("a/./b", ("a", "b")),
        ("..", None),
        ("../a", None),
        ("a/../../b", None),
        ("a/..", None),
        ("/etc/passwd", None),
        ("", None),
        ("./", None),
        ("a\0b", None),
        ("..a/b..", ("..a", "b..")),
    ],
)
def test_which_paths_from_an_archive_are_used(name, parts):
    assert archives.safe_parts(name) == parts


# Extracting


def test_an_archive_with_one_folder_extracts_as_that_folder(tmp_path):
    archive = make_zip(
        tmp_path / "pack.zip", {"top/a.txt": "aaa", "top/sub/b.txt": "bb", "top/sub/c.txt": "c"}
    )
    dst = make_tree(tmp_path / "dst", {"mine.txt": "keep me"})

    job = extract(archive, dst)

    assert job.state is JobState.DONE
    assert job.summary == "Extracted 3 files"
    assert job.seen.states == [JobState.PLANNING, JobState.RUNNING, JobState.DONE]
    assert read_tree(dst) == {
        "mine.txt": "keep me",
        "top": {"a.txt": "aaa", "sub": {"b.txt": "bb", "c.txt": "c"}},
    }
    assert job.result.get_path() == str(dst / "top")
    assert (job.progress.files_done, job.progress.files_total) == (3, 3)
    assert job.progress.bytes_done == job.progress.bytes_total == 6


def test_an_archive_with_many_items_goes_into_a_folder_named_after_it(tmp_path):
    archive = make_tar(tmp_path / "photos.tar.gz", [("1.txt", "one"), ("2.txt", "two")])
    dst = make_tree(tmp_path / "dst", {})

    job = extract(archive, dst)

    assert job.state is JobState.DONE
    assert read_tree(dst) == {"photos": {"1.txt": "one", "2.txt": "two"}}
    assert job.result.get_path() == str(dst / "photos")


def test_an_archive_of_one_file_extracts_as_that_file(tmp_path):
    archive = make_zip(tmp_path / "pack.zip", {"report.txt": "text"})
    dst = make_tree(tmp_path / "dst", {})

    job = extract(archive, dst)

    assert job.state is JobState.DONE
    assert job.summary == "Extracted 1 file"
    assert read_tree(dst) == {"report.txt": "text"}


def test_links_and_permissions_come_out_as_they_went_in(tmp_path):
    archive = tmp_path / "pack.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name, mode in (("top/run.sh", 0o755), ("top/secret", 0o600)):
            info = tarfile.TarInfo(name)
            info.mode, info.size, info.mtime = mode, 2, 1_600_000_000
            tar.addfile(info, io.BytesIO(b"hi"))
        link = tarfile.TarInfo("top/link")
        link.type, link.linkname = tarfile.SYMTYPE, "run.sh"
        tar.addfile(link)
    dst = make_tree(tmp_path / "dst", {})

    job = extract(archive, dst)

    assert job.state is JobState.DONE
    assert stat.S_IMODE((dst / "top/run.sh").stat().st_mode) & 0o700 == 0o700
    assert stat.S_IMODE((dst / "top/secret").stat().st_mode) == 0o600
    assert (dst / "top/secret").stat().st_mtime == 1_600_000_000
    assert os.readlink(dst / "top/link") == "run.sh"


def test_set_user_id_bits_are_not_extracted(tmp_path):
    archive = tmp_path / "pack.tar"
    with tarfile.open(archive, "w") as tar:
        info = tarfile.TarInfo("tool")
        info.mode, info.size = 0o4755, 2
        tar.addfile(info, io.BytesIO(b"hi"))
    dst = make_tree(tmp_path / "dst", {})

    assert extract(archive, dst).state is JobState.DONE
    assert stat.S_IMODE((dst / "tool").stat().st_mode) & 0o7000 == 0


@pytest.mark.parametrize("name", ["../evil.txt", "/tmp/filehouse-evil.txt", "ok/../../evil.txt"])
def test_a_path_that_leaves_the_destination_is_not_written(tmp_path, name):
    archive = make_tar(tmp_path / "pack.tar.gz", [("good.txt", "good"), (name, "evil")])
    dst = make_tree(tmp_path / "work" / "dst", {})

    job = extract(archive, dst)

    assert job.state is JobState.FAILED
    assert job.summary == "Extracted 1 of 2 files, 1 left out"
    assert "unsafe path" in job.errors[0].message
    assert job.errors[0].path == name
    # The file that is safe is there, and nothing was written anywhere else
    assert read_tree(tmp_path / "work") == {"dst": {"good.txt": "good"}}
    assert not os.path.exists("/tmp/filehouse-evil.txt")
    assert not (tmp_path / "evil.txt").exists()


def test_an_archive_with_only_unsafe_paths_makes_nothing(tmp_path):
    archive = make_tar(tmp_path / "pack.tar.gz", [("../evil.txt", "evil")])
    dst = make_tree(tmp_path / "dst", {})

    job = extract(archive, dst)

    assert job.state is JobState.FAILED
    assert read_tree(dst) == {}
    assert not (tmp_path / "evil.txt").exists()


@pytest.mark.parametrize("absolute", [True, False])
def test_a_link_from_the_archive_is_not_written_through(tmp_path, absolute):
    outside = make_tree(tmp_path / "outside", {"mine.txt": "keep me"})
    target = str(outside) if absolute else "../../outside"
    archive = make_tar(
        tmp_path / "pack.tar.gz",
        [
            ("top/way", ("link", target)),
            ("top/way/pwned.txt", "evil"),
            ("top/way/mine.txt", "evil"),
            ("top/good.txt", "good"),
        ],
    )
    dst = make_tree(tmp_path / "dst", {})

    job = extract(archive, dst)

    assert job.state is JobState.FAILED
    assert read_tree(outside) == {"mine.txt": "keep me"}
    assert read_tree(dst)["top"]["good.txt"] == "good"
    assert os.readlink(dst / "top/way") == target
    assert len(job.errors) == 2
    assert all("not a folder" in error.message for error in job.errors)


def test_a_folder_entry_does_not_follow_a_link_either(tmp_path):
    outside = make_tree(tmp_path / "outside", {})
    archive = make_tar(
        tmp_path / "pack.tar.gz",
        [("top/way", ("link", str(outside))), ("top/way/made", None), ("top/way", None)],
    )
    dst = make_tree(tmp_path / "dst", {})

    job = extract(archive, dst)

    assert job.state is JobState.FAILED
    assert read_tree(outside) == {}


def test_a_hard_link_to_outside_the_destination_is_refused(tmp_path):
    make_tree(tmp_path / "outside", {"secret.txt": "secret"})
    archive = make_tar(
        tmp_path / "pack.tar.gz",
        [
            ("top/good.txt", "good"),
            ("top/copy", ("hardlink", "../outside/secret.txt")),
            ("top/again", ("hardlink", "top/good.txt")),
        ],
    )
    dst = make_tree(tmp_path / "dst", {})

    job = extract(archive, dst)

    assert job.state is JobState.FAILED
    assert [error.name for error in job.errors] == ["copy"]
    assert read_tree(dst) == {"top": {"good.txt": "good", "again": "good"}}
    assert (tmp_path / "outside/secret.txt").stat().st_nlink == 1


def test_a_special_file_is_left_out_and_said_so(tmp_path):
    archive = make_tar(
        tmp_path / "pack.tar.gz", [("top/pipe", ("fifo",)), ("top/good.txt", "good")]
    )
    dst = make_tree(tmp_path / "dst", {})

    job = extract(archive, dst)

    assert job.state is JobState.FAILED
    assert job.summary == "Extracted 1 of 2 files, 1 left out"
    assert "special file" in job.errors[0].message
    assert read_tree(dst) == {"top": {"good.txt": "good"}}


def test_an_entry_listed_twice_does_not_replace_the_first(tmp_path):
    archive = make_tar(tmp_path / "pack.tar.gz", [("top/a.txt", "first"), ("top/a.txt", "second")])
    dst = make_tree(tmp_path / "dst", {})

    job = extract(archive, dst)

    assert job.state is JobState.FAILED
    assert "twice" in job.errors[0].message
    assert read_tree(dst) == {"top": {"a.txt": "first"}}


def test_a_taken_name_fails_when_nobody_can_be_asked(tmp_path):
    archive = make_zip(tmp_path / "pack.zip", {"top/a.txt": "new"})
    dst = make_tree(tmp_path / "dst", {"top": {"a.txt": "old", "mine.txt": "keep me"}})

    job = extract(archive, dst)

    assert job.state is JobState.FAILED
    assert job.error.message == "already exists"
    assert job.error.path == str(dst / "top")
    assert read_tree(dst) == {"top": {"a.txt": "old", "mine.txt": "keep me"}}


def test_a_taken_name_can_be_kept_beside_the_new_one(tmp_path):
    archive = make_zip(tmp_path / "pack.zip", {"top/a.txt": "new"})
    dst = make_tree(tmp_path / "dst", {"top": {"a.txt": "old"}})
    prompt = answering(Answer.KEEP_BOTH)

    job = extract(archive, dst, prompt)

    assert job.state is JobState.DONE
    assert isinstance(prompt.asked[0], NameTaken)
    assert prompt.asked[0].destination_is_dir
    assert read_tree(dst) == {"top": {"a.txt": "old"}, "top (2)": {"a.txt": "new"}}
    assert job.result.get_path() == str(dst / "top (2)")


def test_a_taken_name_is_never_replaced_whatever_the_answer(tmp_path):
    archive = make_zip(tmp_path / "pack.zip", {"report.txt": "new"})
    dst = make_tree(tmp_path / "dst", {"report.txt": "old"})

    job = extract(archive, dst, answering(Answer.CANCEL))

    assert job.state is JobState.CANCELLED
    assert read_tree(dst) == {"report.txt": "old"}


def test_a_single_file_whose_name_is_taken_at_the_last_moment_asks_again(tmp_path):
    archive = make_zip(tmp_path / "pack.zip", {"report.txt": "new"})
    dst = make_tree(tmp_path / "dst", {})

    def appear(job):
        # After planning found the name free, before the file is made
        if job.state is JobState.RUNNING and not (dst / "report.txt").exists():
            (dst / "report.txt").write_text("somebody else's")

    job = extract(archive, dst, answering(Answer.KEEP_BOTH), hook=appear)

    assert job.state is JobState.DONE
    assert read_tree(dst) == {"report.txt": "somebody else's", "report (2).txt": "new"}


def test_cancel_removes_only_the_file_being_written(tmp_path):
    big = os.urandom(600_000)
    archive = make_tar(
        tmp_path / "pack.tar.gz", [("top/1.bin", b"first"), ("top/2.bin", big), ("top/3.bin", b"x")]
    )
    dst = make_tree(tmp_path / "dst", {"mine.txt": "keep me"})

    def hook(job):
        if job.progress.files_done == 1 and job.progress.bytes_done >= 100_000:
            job.cancel()

    job = extract(archive, dst, hook=hook)

    assert job.state is JobState.CANCELLED
    assert job.summary == "Cancelled after 1 of 3 files"
    assert JobState.DONE not in job.seen.states
    # The finished file stays, the partial one is gone, and nothing else was touched
    assert sorted(os.listdir(dst / "top")) == ["1.bin"]
    assert (dst / "mine.txt").read_text() == "keep me"
    assert not leftovers(tmp_path)


def test_a_damaged_archive_fails_and_names_where(tmp_path):
    whole = make_tar(
        tmp_path / "whole.tar.gz", [("top/1.bin", b"first"), ("top/2.bin", os.urandom(300_000))]
    )
    archive = tmp_path / "pack.tar.gz"
    archive.write_bytes(whole.read_bytes()[:150_000])
    dst = make_tree(tmp_path / "dst", {})

    job = extract(archive, dst)

    assert job.state is JobState.FAILED
    assert JobState.DONE not in job.seen.states
    assert job.error.step == "extract"
    assert job.error.path


def test_something_that_is_not_an_archive_fails(tmp_path):
    archive = tmp_path / "pack.zip"
    archive.write_text("this is not a zip file at all")
    dst = make_tree(tmp_path / "dst", {})

    job = extract(archive, dst)

    assert job.state is JobState.FAILED
    assert job.error.path == str(archive)
    assert read_tree(dst) == {}


def test_full_progress_is_not_done(tmp_path):
    archive = make_zip(tmp_path / "pack.zip", {"top/a.txt": "aaa"})
    dst = make_tree(tmp_path / "dst", {})

    job = extract(archive, dst)

    full = [index for index, p in enumerate(job.seen.progress) if p.fraction == 1.0]
    assert full and job.seen.progress[-1].fraction == 1.0
    assert job.seen.states[-1] is JobState.DONE and job.seen.states.count(JobState.DONE) == 1


# Password-protected archives


@pytest.fixture(params=["zipcrypt", "aes256"])
def protected(request, tmp_path):
    import libarchive

    path = tmp_path / "secret.zip"
    with libarchive.file_writer(
        str(path), "zip", options=f"zip:encryption={request.param}", passphrase="open sesame"
    ) as archive:
        archive.add_file_from_memory("top/a.txt", 5, b"hello")
    return path


def test_a_protected_archive_fails_when_nobody_can_be_asked(tmp_path, protected):
    dst = make_tree(tmp_path / "dst", {})

    job = extract(protected, dst)

    assert job.state is JobState.FAILED
    assert job.error.message == "is protected with a password"
    assert read_tree(dst) == {}


def test_a_protected_archive_asks_until_the_password_is_right(tmp_path, protected):
    dst = make_tree(tmp_path / "dst", {})
    prompt = answering(
        Reply(Answer.PASSWORD, text="wrong"), Reply(Answer.PASSWORD, text="open sesame")
    )

    job = extract(protected, dst, prompt)

    assert job.state is JobState.DONE
    assert [type(question) for question in prompt.asked] == [Password, Password]
    assert [question.wrong for question in prompt.asked] == [False, True]
    assert read_tree(dst) == {"top": {"a.txt": "hello"}}
    assert JobState.WAITING in job.seen.states


def test_cancelling_the_password_question_cancels_the_job(tmp_path, protected):
    dst = make_tree(tmp_path / "dst", {})

    job = extract(protected, dst, answering(Answer.CANCEL))

    assert job.state is JobState.CANCELLED
    assert read_tree(dst) == {}


# Compressing


def unpacked(archive, into):
    """What an archive holds, read with the standard library."""
    into.mkdir()
    if str(archive).endswith(".zip"):
        with zipfile.ZipFile(archive) as opened:
            opened.extractall(into)
    else:
        with tarfile.open(archive) as opened:
            opened.extractall(into, filter="tar")
    return read_tree(into)


@pytest.mark.parametrize("format, name", [("zip", "pack.zip"), ("tar.gz", "pack.tar.gz")])
def test_compressing_keeps_the_originals_and_holds_all_of_them(tmp_path, format, name):
    src = make_tree(tmp_path / "work", {"src": TREE, "note.txt": "note", "empty": {}})
    before = read_tree(src)

    job = compress([src / "src", src / "note.txt", src / "empty"], src, name, format)

    assert job.state is JobState.DONE
    assert job.summary == "Compressed 4 files"
    assert job.seen.states == [JobState.PLANNING, JobState.RUNNING, JobState.DONE]
    assert job.result.get_path() == str(src / name)
    assert unpacked(src / name, tmp_path / "check") == {
        "src": TREE,
        "note.txt": "note",
        "empty": {},
    }
    (src / name).unlink()
    assert read_tree(src) == before
    assert not leftovers(tmp_path)
    assert job.progress.bytes_done == job.progress.bytes_total == 10


def test_a_link_is_stored_as_a_link(tmp_path):
    src = make_tree(tmp_path / "work", {"src": {"a.txt": "aaa", "way": ("link", "a.txt")}})

    job = compress([src / "src"], src, "pack.tar.gz", "tar.gz")

    assert job.state is JobState.DONE
    assert unpacked(src / "pack.tar.gz", tmp_path / "check") == {
        "src": {"a.txt": "aaa", "way": ("link", "a.txt")}
    }


def test_a_link_to_a_folder_is_not_walked_into(tmp_path):
    make_tree(tmp_path / "elsewhere", {"big.txt": "not mine to pack"})
    src = make_tree(tmp_path / "work", {"src": {"way": ("link", str(tmp_path / "elsewhere"))}})

    job = compress([src / "src"], src, "pack.zip")

    assert job.state is JobState.DONE
    with zipfile.ZipFile(src / "pack.zip") as opened:
        assert sorted(opened.namelist()) == ["src/", "src/way"]


def test_a_taken_archive_name_fails_when_nobody_can_be_asked(tmp_path):
    src = make_tree(tmp_path / "work", {"a.txt": "aaa", "pack.zip": "not really a zip"})

    job = compress([src / "a.txt"], src, "pack.zip")

    assert job.state is JobState.FAILED
    assert job.error.message == "already exists"
    assert read_tree(src) == {"a.txt": "aaa", "pack.zip": "not really a zip"}
    assert not leftovers(tmp_path)


def test_a_taken_archive_name_can_be_kept_beside_the_new_one(tmp_path):
    src = make_tree(tmp_path / "work", {"a.txt": "aaa", "pack.tar.gz": "mine"})

    job = compress([src / "a.txt"], src, "pack.tar.gz", "tar.gz", answering(Answer.KEEP_BOTH))

    assert job.state is JobState.DONE
    assert (src / "pack.tar.gz").read_text() == "mine"
    assert job.result.get_path() == str(src / "pack (2).tar.gz")
    assert tarfile.is_tarfile(src / "pack (2).tar.gz")


def test_an_archive_name_taken_at_the_last_moment_asks_again(tmp_path):
    src = make_tree(tmp_path / "work", {"a.txt": "aaa"})

    def appear(job):
        if job.progress.files_done == 1 and not (src / "pack.zip").exists():
            (src / "pack.zip").write_text("somebody else's")

    job = compress([src / "a.txt"], src, "pack.zip", "zip", answering(Answer.KEEP_BOTH), appear)

    assert job.state is JobState.DONE
    assert (src / "pack.zip").read_text() == "somebody else's"
    assert zipfile.is_zipfile(src / "pack (2).zip")
    assert not leftovers(tmp_path)


def test_cancelling_a_compression_leaves_no_archive_and_every_original(tmp_path):
    src = make_tree(tmp_path / "work", {"1.txt": "one"})
    (src / "2.bin").write_bytes(os.urandom(600_000))
    (src / "3.txt").write_text("three")

    def hook(job):
        if job.progress.files_done == 1 and job.progress.bytes_done >= 100_000:
            job.cancel()

    job = compress([src / "1.txt", src / "2.bin", src / "3.txt"], src, "pack.zip", hook=hook)

    assert job.state is JobState.CANCELLED
    assert job.summary == "Cancelled after 1 of 3 files"
    assert sorted(os.listdir(src)) == ["1.txt", "2.bin", "3.txt"]
    assert (src / "2.bin").stat().st_size == 600_000


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read everything")
def test_a_file_that_cannot_be_read_is_left_out_and_named(tmp_path):
    src = make_tree(tmp_path / "work", {"src": {"a.txt": "aaa", "locked.txt": "no"}})
    (src / "src/locked.txt").chmod(0)

    job = compress([src / "src"], src, "pack.zip")

    assert job.state is JobState.FAILED
    assert job.summary == "Compressed 1 of 2 files, 1 left out"
    assert job.error.path == str(src / "src/locked.txt")
    assert job.error.message == "permission denied"
    with zipfile.ZipFile(src / "pack.zip") as opened:
        assert sorted(opened.namelist()) == ["src/", "src/a.txt"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read everything")
def test_nothing_readable_makes_no_archive(tmp_path):
    src = make_tree(tmp_path / "work", {"locked.txt": "no"})
    (src / "locked.txt").chmod(0)

    job = compress([src / "locked.txt"], src, "pack.zip")

    assert job.state is JobState.FAILED
    assert sorted(os.listdir(src)) == ["locked.txt"]


def test_an_archive_needs_a_name(tmp_path):
    src = make_tree(tmp_path / "work", {"a.txt": "aaa"})

    job = compress([src / "a.txt"], src, "../pack.zip")

    assert job.state is JobState.FAILED
    assert sorted(os.listdir(tmp_path)) == ["work"]


@pytest.mark.parametrize(
    "names, folder, expected",
    [
        (["report.pdf"], "Documents", "report.zip"),
        (["Photos"], "Pictures", "Photos.zip"),
        (["a.txt", "b.txt"], "Documents", "Documents.zip"),
        (["a.txt", "b.txt"], "", "Archive.zip"),
        ([".bashrc"], "home", ".bashrc.zip"),
    ],
)
def test_what_a_new_archive_is_called(names, folder, expected):
    assert archives.archive_name(names, folder, archives.FORMATS[0]) == expected


@pytest.mark.parametrize(
    "name, expected",
    [("pack.zip", "pack"), ("pack.tar.gz", "pack"), ("pack", "pack (extracted)")],
)
def test_what_the_folder_of_an_archive_is_called(name, expected):
    assert archives.folder_name(name) == expected
