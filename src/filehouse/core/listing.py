"""Directory enumeration and the file records the views show."""

import re
from dataclasses import dataclass

from gi.repository import Gio, GLib

# Only what the views need. `standard::fast-content-type` guesses from the
# name; the full `standard::content-type` (and `standard::icon`, which is
# derived from it) reads the start of every file, which is too slow for
# large folders.
ATTRIBUTES = ",".join(
    [
        "standard::name",
        "standard::display-name",
        "standard::type",
        "standard::is-hidden",
        "standard::is-backup",
        "standard::is-symlink",
        "standard::size",
        "standard::fast-content-type",
        "time::modified",
    ]
)

BATCH_SIZE = 200

_DIGITS = re.compile(r"(\d+)")


class ListingError(Exception):
    """A folder could not be read. The message is fit to show to the user."""


@dataclass(frozen=True, slots=True)
class FileInfo:
    name: str
    display_name: str
    is_dir: bool
    is_hidden: bool
    is_symlink: bool
    size: int
    modified: int  # seconds since the epoch, 0 when unknown
    content_type: str
    sort_key: tuple


def natural_key(name):
    """A sort key that ignores case and orders "file2" before "file10"."""
    parts = _DIGITS.split(name.casefold())
    # re.split with a group alternates text and digits, starting with text,
    # so two keys always compare str with str and int with int
    return tuple(int(part) if index % 2 else part for index, part in enumerate(parts))


def info_from_gio(info):
    name = info.get_name()
    display_name = info.get_display_name()
    return FileInfo(
        name=name,
        display_name=display_name,
        is_dir=info.get_file_type() == Gio.FileType.DIRECTORY,
        is_hidden=info.get_is_hidden() or info.get_is_backup(),
        is_symlink=info.get_is_symlink(),
        size=info.get_size(),
        modified=info.get_attribute_uint64("time::modified"),
        content_type=info.get_attribute_string("standard::fast-content-type")
        or "application/octet-stream",
        sort_key=natural_key(display_name),
    )


def list_directory(location, on_batch, cancellable=None, batch_size=BATCH_SIZE):
    """Enumerate a folder, calling `on_batch(list[FileInfo])` as entries arrive.

    Blocks, so call it from a worker thread. Returns quietly when cancelled
    and raises ListingError when the folder can't be read.
    """
    try:
        enumerator = location.enumerate_children(
            ATTRIBUTES, Gio.FileQueryInfoFlags.NONE, cancellable
        )
    except GLib.Error as error:
        if _is_cancelled(error):
            return
        raise ListingError(error.message) from error

    batch = []
    try:
        while True:
            info = enumerator.next_file(cancellable)
            if info is None:
                break
            batch.append(info_from_gio(info))
            if len(batch) >= batch_size:
                on_batch(batch)
                batch = []
    except GLib.Error as error:
        if _is_cancelled(error):
            return
        raise ListingError(error.message) from error
    finally:
        try:
            enumerator.close(None)
        except GLib.Error:
            pass
    if batch:
        on_batch(batch)


def _is_cancelled(error):
    return error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED)
