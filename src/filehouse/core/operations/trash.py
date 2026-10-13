"""Moving to the trash, and finding there what was moved."""

from datetime import datetime

from gi.repository import Gio, GLib

from filehouse.core import locations
from filehouse.core.errors import has_code

_LISTED = "standard::name,trash::orig-path,trash::deletion-date"
# The trash writes the time down in whole seconds, and its clock is the same as ours
_SLACK_SECONDS = 2


class NoTrash(Exception):
    """The file system this item is on has no trash."""


def trash(step, cancellable):
    """Trash the item itself: a link goes to the trash, not what it points to."""
    try:
        _gio_trash(step.source, cancellable)
    except GLib.Error as error:
        if has_code(error, Gio.IOErrorEnum.NOT_SUPPORTED):
            raise NoTrash from error
        raise


def _gio_trash(location, cancellable):
    location.trash(cancellable)


def find(originals, since, cancellable):
    """Where in the trash the items are that were at `originals` and went there at
    `since` (by the clock) or later: {original path: trashed item}. One that is not
    found is not among them: restored already, or deleted from the trash.

    Of several that were at one place, the one trashed last is it."""
    wanted = {original.get_path() for original in originals}
    earliest = datetime.fromtimestamp(since - _SLACK_SECONDS)
    found = {}
    for item, path, deleted in _list(cancellable):
        if path not in wanted or deleted is None or deleted < earliest:
            continue
        if path not in found or deleted >= found[path][1]:
            found[path] = (item, deleted)
    return {path: item for path, (item, _deleted) in found.items()}


def _list(cancellable):
    """Everything in the trash: (the item, where it was, when it was trashed)."""
    bin_ = locations.trash()
    enumerator = bin_.enumerate_children(
        _LISTED, Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, cancellable
    )
    listed = []
    try:
        while (info := enumerator.next_file(cancellable)) is not None:
            stamp = info.get_attribute_string("trash::deletion-date")
            try:
                deleted = datetime.fromisoformat(stamp) if stamp else None
            except ValueError:
                deleted = None
            listed.append(
                (
                    bin_.get_child(info.get_name()),
                    info.get_attribute_byte_string("trash::orig-path"),
                    deleted,
                )
            )
    finally:
        try:
            enumerator.close(None)
        except GLib.Error:
            pass
    return listed
