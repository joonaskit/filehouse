"""Changing who may read, write and run files, by a handful of presets.

Finishes at once and needs no job, like new folder and rename, and reports
failure with the same OperationError as everything else.
"""

from dataclasses import dataclass

from gi.repository import Gio, GLib

from filehouse.core.errors import OperationError

_STEP = "change permissions"
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


@dataclass(frozen=True)
class Outcome:
    changed: int = 0
    # Left as they were, by design and not by failure: folders, for a preset
    # that is only for files
    skipped: int = 0
    errors: tuple = ()  # of OperationError, one per item that could not be changed


def apply(preset, selected, cancellable=None):
    """Give each of the locations `selected` the permissions of `preset`.

    Only the items themselves: nothing inside a folder is touched. A link is
    never followed, so what it points to is never changed; a link has no
    permissions of its own, which is reported as an error for that link.
    Carries on after a failure and reports every one. Blocks: for a worker thread.
    """
    changed = skipped = 0
    errors = []
    for location in selected:
        if cancellable is not None and cancellable.is_cancelled():
            break
        try:
            info = location.query_info("standard::type,unix::mode", _NOFOLLOW, cancellable)
            kind = info.get_file_type()
            if kind == Gio.FileType.SYMBOLIC_LINK:
                errors.append(
                    OperationError.at(location, _STEP, "a link has no permissions of its own")
                )
                continue
            is_dir = kind == Gio.FileType.DIRECTORY
            mode = preset.mode_for(is_dir)
            if mode is None:
                skipped += 1
                continue
            if is_dir and info.has_attribute("unix::mode"):
                mode |= info.get_attribute_uint32("unix::mode") & _KEPT_ON_FOLDERS
            _set_mode(location, mode, cancellable)
            changed += 1
        except GLib.Error as error:
            if error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                break
            errors.append(OperationError.from_glib(error, location, _STEP))
    return Outcome(changed, skipped, tuple(errors))


def _set_mode(location, mode, cancellable):
    location.set_attribute_uint32("unix::mode", mode, _NOFOLLOW, cancellable)
