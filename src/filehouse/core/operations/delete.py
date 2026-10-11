"""Permanent delete, one item per step."""

from gi.repository import Gio, GLib

from filehouse.core.errors import has_code


def delete(step, cancellable):
    """Remove one file, link or empty folder. Never recursive, and never through a link.

    The one exception is a `whole` step, for an item in the trash, which the trash
    removes with everything in it. False when such an item turned out to be a folder
    that has to be emptied file by file after all."""
    try:
        step.source.delete(cancellable)
    except GLib.Error as error:
        if step.whole and has_code(error, Gio.IOErrorEnum.NOT_EMPTY):
            return False
        raise
    return True
