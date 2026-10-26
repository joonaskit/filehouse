import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import pytest
from helpers import gfile, not_supported

from filehouse.core.operations import copy, job, trash


@pytest.fixture
def real_other_device(tmp_path):
    """A folder on a different file system than tmp_path, or skip."""
    try:
        path = Path(tempfile.mkdtemp(prefix="filehouse-test-", dir="/dev/shm"))
    except OSError:
        pytest.skip("no second file system to test with")
    if path.stat().st_dev == tmp_path.stat().st_dev:
        shutil.rmtree(path)
        pytest.skip("no second file system to test with")
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(params=["real", "forced"])
def other_device(request, tmp_path, monkeypatch):
    """A destination that can't be reached with a rename: a real second file system
    where there is one, and always a folder where renames are made to fail the same way."""
    if request.param == "real":
        return request.getfixturevalue("real_other_device")
    real_rename = copy._rename_item

    def rename(source, dest, flags, cancellable):
        # Only the move of an item across: the temporary file is renamed beside its destination
        if source.get_basename().startswith(copy._TEMP_PREFIX):
            return real_rename(source, dest, flags, cancellable)
        return not_supported()

    monkeypatch.setattr(copy, "_rename_item", rename)
    path = tmp_path / "other"
    path.mkdir()
    return path


@pytest.fixture
def slow_copy(monkeypatch):
    """Copy file contents in small chunks, so a test can act in the middle of a file.
    Like Gio, it leaves the partial file behind when it is cancelled."""

    def chunked(source, dest, _flags, cancellable, progress):
        total = os.path.getsize(source.get_path())
        done = 0
        with open(source.get_path(), "rb") as reader, open(dest.get_path(), "xb") as writer:
            while chunk := reader.read(1024):
                if cancellable is not None:
                    cancellable.set_error_if_cancelled()
                writer.write(chunk)
                done += len(chunk)
                progress(done, total)

    monkeypatch.setattr(copy, "_gio_copy", chunked)


@pytest.fixture
def kept():
    """What the stand-in trash wrote down: (its name there, where it was, when it came)."""
    return []


@pytest.fixture
def bin_(tmp_path, monkeypatch, kept):
    """A folder that stands in for the trash, and keeps what the trash keeps: where
    each item was and when it came."""
    folder = tmp_path / "bin"
    folder.mkdir()

    def put(location, _cancellable):
        name = f"{len(kept)}-{location.get_basename()}"
        os.rename(location.get_path(), folder / name)
        kept.append((name, location.get_path(), datetime.now().replace(microsecond=0)))

    def listed(_cancellable):
        return [
            (gfile(folder / name), path, when)
            for name, path, when in kept
            if (folder / name).exists()
        ]

    monkeypatch.setattr(trash, "_gio_trash", put)
    monkeypatch.setattr(trash, "_list", listed)
    monkeypatch.setattr(job, "_TRASH_SETTLES_SECONDS", 0)
    return folder
