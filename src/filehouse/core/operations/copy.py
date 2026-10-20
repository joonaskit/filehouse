"""Running the steps of a copy or a move.

A file is copied to a temporary name next to its destination and renamed into
place once it is complete. A cancelled or failed copy therefore leaves nothing
under the real name, and a file being replaced stays whole until its
replacement is. The temporary file is always ours to remove.
"""

import secrets

from gi.repository import Gio, GLib

from filehouse.core import locations
from filehouse.core.errors import has_code
from filehouse.core.operations.conflicts import Exists

_NOFOLLOW = Gio.FileCopyFlags.NOFOLLOW_SYMLINKS
# Never let Gio turn a move into its own copy-and-delete
_RENAME = _NOFOLLOW | Gio.FileCopyFlags.NO_FALLBACK_FOR_MOVE
_COPY = _NOFOLLOW | Gio.FileCopyFlags.ALL_METADATA

_TEMP_PREFIX = ".filehouse-"
_TEMP_SUFFIX = ".part"

# Errors that mean "this folder can't be moved in one go": do it item by item
_NOT_IN_ONE_GO = (
    Gio.IOErrorEnum.NOT_SUPPORTED,
    Gio.IOErrorEnum.EXISTS,
    Gio.IOErrorEnum.WOULD_MERGE,
    Gio.IOErrorEnum.WOULD_RECURSE,
)


def make_folder(step, cancellable):
    dest = step.dest
    try:
        dest.make_directory(cancellable)
    except GLib.Error as error:
        if not has_code(error, Gio.IOErrorEnum.EXISTS):
            raise
        existing = locations.file_type(dest)
        if existing is None:
            raise
        if existing != Gio.FileType.DIRECTORY or not step.merges():
            raise Exists(existing) from None
        step.overwrite = True
        return
    step.created = True


def transfer_file(step, move, cancellable, on_bytes):
    """Copy one file or link to its destination; in a move, then remove the source."""
    source, dest = step.source, step.dest
    if not step.copied:
        if not step.overwrite:
            existing = locations.file_type(dest, cancellable)
            if existing is not None:
                raise Exists(existing)
        # Never `| 0`: some PyGObject versions make that a plain int, which Gio refuses
        flags = (_RENAME | Gio.FileCopyFlags.OVERWRITE) if step.overwrite else _RENAME
        if move:
            try:
                _rename_item(source, dest, flags, cancellable)
                return
            except GLib.Error as error:
                if has_code(error, Gio.IOErrorEnum.EXISTS):
                    raise _exists(dest, error) from None
                if not has_code(error, Gio.IOErrorEnum.NOT_SUPPORTED):
                    raise
                # Another file system: copy, and remove the source once the copy is complete

        temp = _copy_to_temp(source, dest.get_parent(), cancellable, on_bytes)
        try:
            # Not cancellable: a complete copy is put in place, not thrown away
            temp.move(dest, flags, None, None, None)
        except GLib.Error as error:
            _discard(temp)
            if has_code(error, Gio.IOErrorEnum.EXISTS):
                raise _exists(dest, error) from None
            raise
        step.copied = True
    if move:
        source.delete(None)


def rename(step, cancellable):
    """Move a whole folder in one go. False when it has to be done item by item instead."""
    try:
        if locations.file_type(step.dest, cancellable) is not None:
            return False
        _rename_item(step.source, step.dest, _RENAME, cancellable)
    except GLib.Error as error:
        if any(has_code(error, code) for code in _NOT_IN_ONE_GO):
            return False
        raise
    return True


def restore(step, cancellable):
    """Move a trashed item back to where it was, making the folder it was in if that is gone."""
    dest = step.dest
    ensure_folder(step.folder, cancellable)
    existing = locations.file_type(dest, cancellable)
    if existing is not None:
        raise Exists(existing)
    try:
        _rename_item(step.source, dest, _RENAME, cancellable)
    except GLib.Error as error:
        if has_code(error, Gio.IOErrorEnum.EXISTS):
            raise _exists(dest, error) from None
        raise


def ensure_folder(folder, cancellable):
    """Make `folder`, and the folders above it, when it is gone: for what is put back
    where it was."""
    try:
        folder.make_directory_with_parents(cancellable)
    except GLib.Error as error:
        if not has_code(error, Gio.IOErrorEnum.EXISTS):
            raise


def finish_folder(step, move):
    """After a folder's contents: copy its metadata, and in a move remove the emptied source."""
    made = step.parent
    if made.created:
        try:
            step.source.copy_attributes(made.dest, _COPY, None)
        except GLib.Error:
            # Best effort: Gio sets what it can and then complains about the rest
            pass
    if move:
        try:
            # Never recursive: a folder that still holds anything stays where it is
            step.source.delete(None)
        except GLib.Error as error:
            if not has_code(error, Gio.IOErrorEnum.NOT_EMPTY):
                raise


def _rename_item(source, dest, flags, cancellable):
    source.move(dest, flags, cancellable, None, None)


def _gio_copy(source, dest, flags, cancellable, progress):
    source.copy(dest, flags, cancellable, progress, None)


def _copy_to_temp(source, folder, cancellable, on_bytes):
    def progress(current, _total, *_data):
        on_bytes(current)

    while True:
        temp = folder.get_child(f"{_TEMP_PREFIX}{secrets.token_hex(4)}{_TEMP_SUFFIX}")
        try:
            _gio_copy(source, temp, _COPY, cancellable, progress)
        except GLib.Error as error:
            if has_code(error, Gio.IOErrorEnum.EXISTS):
                # Somebody else's file has this name: leave it and pick another
                continue
            _discard(temp)
            raise
        return temp


def _discard(temp):
    try:
        temp.delete(None)
    except GLib.Error:
        pass


def _exists(dest, error):
    existing = locations.file_type(dest)
    if existing is None:
        return error
    return Exists(existing)
