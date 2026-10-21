"""Changing who may read, write and run files, by a handful of presets.

A job like every other (`PermissionsJob`): queued, with progress, cancel, Skip and
Retry for an item that could not be changed, and a summary. It reports failure with
the same OperationError as everything else.
"""

from dataclasses import dataclass, replace

from gi.repository import Gio, GLib

from filehouse.core.errors import OperationError, is_cancelled
from filehouse.core.operations.conflicts import Answer
from filehouse.core.operations.job import _VERBS, Job, JobKind, JobState, _Cancelled

_STEP = "change the permissions of"
_NOFOLLOW = Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS
# A folder keeps these when its permissions change: who new files in it belong to
# (set-group-id) and who may delete from it (sticky). A file loses its special
# bits: a set-user-id file made writable by its group would be a hole
_KEPT_ON_FOLDERS = 0o3000


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    file_mode: int
    # With the "open" bit wherever there is a "read" bit: without it a folder
    # can't be entered. None for a preset that says nothing about folders
    folder_mode: int | None

    def mode_for(self, is_dir):
        """The mode this preset gives a file or a folder, or None when it leaves it alone."""
        return self.folder_mode if is_dir else self.file_mode


PRESETS = (
    Preset("private", "Private", 0o600, 0o700),
    Preset("shared", "Shared", 0o664, 0o775),
    Preset("executable", "Make executable", 0o755, None),
    Preset("read-only", "Read-only", 0o444, 0o555),
)


class _NoPermissions(OperationError):
    """An item that has no permissions to change: asking again would not help."""


class PermissionsJob(Job):
    """Give each of the locations `sources` the permissions of `preset`.

    Only the items themselves: nothing inside a folder is touched. A link is never
    followed, so what it points to is never changed; a link has no permissions of its
    own, which is reported as an error for that link and not asked about. Any other
    failure is asked about (Skip or Retry), and without a prompt it is skipped.
    """

    def __init__(self, preset, sources, *, prompt=None, on_change=None):
        super().__init__(JobKind.PERMISSIONS, sources, prompt=prompt, on_change=on_change)
        self.preset = preset
        self.changed = 0
        # Left as they were, by design and not by failure: folders, for a preset that
        # is only for files
        self.left_alone = 0
        self._failed = []  # what could be tried again
        self._complete = False

    @property
    def can_retry(self):
        return self.state is JobState.FAILED and bool(self._failed)

    def retry(self):
        if not self.can_retry:
            return None
        job = PermissionsJob(
            self.preset, self._failed, prompt=self._prompt, on_change=self.on_change
        )
        job.is_undo = self.is_undo
        return job

    def _work(self):
        self._set_progress(files_total=len(self.sources), planned=True)
        self._set_state(JobState.RUNNING)
        for location in self.sources:
            self._check_cancelled()
            self._set_progress(current=location.get_basename())
            self._change(location)
            self._set_progress(files_done=self.progress.files_done + 1)
        self._complete = True

    def _change(self, location):
        while True:
            self._check_cancelled()
            try:
                if _set(self.preset, location, self.cancellable):
                    self.changed += 1
                else:
                    self.left_alone += 1
                return
            except _NoPermissions as error:
                self.errors.append(error)
                return
            except OperationError as failure:
                error = failure
            except GLib.Error as failure:
                if is_cancelled(failure):
                    raise _Cancelled from None
                error = OperationError.from_glib(failure, location, _STEP)
            if self._ask_about(error) is not Answer.RETRY:
                self.errors.append(error)
                self._failed.append(location)
                return

    def _finish(self, cancelled):
        if not cancelled and not self.errors and not self._complete:
            # Unreachable by design, but `done` must never be a guess
            self.errors.append(OperationError("", _STEP, "did not finish"))
        done = self.changed + self.left_alone
        total = len(self.sources)
        verb = _VERBS[self.kind]
        if cancelled:
            state = JobState.CANCELLED
            self.summary = f"Cancelled after {done} of {_items(total)}"
        elif self.errors:
            state = JobState.FAILED
            failed = total - done
            self.summary = f"{verb} {self.changed} of {_items(total)}, {failed} failed"
        else:
            state = JobState.DONE
            if self.left_alone:
                # "Make executable" on a selection with folders in it
                files = "1 file" if self.changed == 1 else f"{self.changed} files"
                self.summary = f"{verb} {files}, folders left as they were"
            else:
                self.summary = f"{verb} {_items(self.changed)}"
        self.progress = replace(self.progress, current="")
        self._set_state(state)


def _items(count):
    return "1 item" if count == 1 else f"{count} items"


def _set(preset, location, cancellable):
    """Give one item the permissions of `preset`. False when the preset leaves it alone."""
    info = location.query_info("standard::type,unix::mode", _NOFOLLOW, cancellable)
    kind = info.get_file_type()
    if kind == Gio.FileType.SYMBOLIC_LINK:
        raise _NoPermissions.at(location, _STEP, "a link has no permissions of its own")
    is_dir = kind == Gio.FileType.DIRECTORY
    mode = preset.mode_for(is_dir)
    if mode is None:
        return False
    if is_dir and info.has_attribute("unix::mode"):
        mode |= info.get_attribute_uint32("unix::mode") & _KEPT_ON_FOLDERS
    _set_mode(location, mode, cancellable)
    return True


def _set_mode(location, mode, cancellable):
    location.set_attribute_uint32("unix::mode", mode, _NOFOLLOW, cancellable)
