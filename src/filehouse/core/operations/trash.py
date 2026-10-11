"""Moving to the trash. Restore is added in phase 5 with the trash view."""

from gi.repository import Gio, GLib

from filehouse.core.errors import has_code


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
