"""Directory enumeration and the file records the views show."""

import re
import stat
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
        "unix::mode",
        # Only set for what is in the trash
        "trash::orig-path",
        # Only set for what is in the list of recent files: the file it stands for, and
        # when it was last used
        "standard::target-uri",
        "recent::modified",
    ]
)

BATCH_SIZE = 200

# All that counting a folder's entries needs
_COUNT_ATTRIBUTES = "standard::name,standard::is-hidden,standard::is-backup"

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
    original_path: str | None = None  # where a trashed item was, for restoring it
    mode: int | None = None  # permission bits, None where there are none
    target_uri: str | None = None  # the file an entry of the recent files stands for
    used: int = 0  # when an entry of the recent files was last used, 0 elsewhere

    @property
    def folder(self):
        """For a result of a search, the folder it is in, as a path below the one that
        was searched ("sub/deep"). Empty for an entry of the listed folder itself."""
        return self.name.rpartition("/")[0]

    @property
    def shown_date(self):
        """The date the Modified column shows: in the recent files when it was used, which
        is also their order, and elsewhere when the file changed."""
        return self.used or self.modified


@dataclass(frozen=True, slots=True)
class Count:
    """How many entries a folder has directly in it."""

    shown: int
    hidden: int


def natural_key(name):
    """A sort key that ignores case and orders "file2" before "file10"."""
    parts = _DIGITS.split(name.casefold())
    # re.split with a group alternates text and digits, starting with text,
    # so two keys always compare str with str and int with int
    return tuple(int(part) if index % 2 else part for index, part in enumerate(parts))


def _content_type(info, display_name):
    content_type = info.get_attribute_string("standard::fast-content-type")
    if (not content_type or content_type == "application/octet-stream") and info.has_attribute(
        "standard::target-uri"
    ):
        # The list of recent files has no guess from the name of its own
        content_type = Gio.content_type_guess(display_name, None)[0]
    return content_type or "application/octet-stream"


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
        content_type=_content_type(info, display_name),
        sort_key=natural_key(display_name),
        original_path=info.get_attribute_byte_string("trash::orig-path"),
        mode=(
            info.get_attribute_uint32("unix::mode") & 0o7777
            if info.has_attribute("unix::mode")
            else None
        ),
        target_uri=info.get_attribute_string("standard::target-uri"),
        used=max(0, info.get_attribute_int64("recent::modified")),
    )


def mode_text(mode):
    """Permission bits as `ls` writes them: "rwxr-xr-x". Empty when there are none."""
    return "" if mode is None else stat.filemode(mode)[1:]


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


def count_children(location, cancellable=None):
    """Count what is directly in a folder. Blocks, so call it from a worker thread.

    Returns a Count, or None when the folder can't be read or the count was cancelled.
    """
    shown = hidden = 0
    try:
        enumerator = location.enumerate_children(
            _COUNT_ATTRIBUTES, Gio.FileQueryInfoFlags.NONE, cancellable
        )
    except GLib.Error:
        return None
    try:
        while (info := enumerator.next_file(cancellable)) is not None:
            if info.get_is_hidden() or info.get_is_backup():
                hidden += 1
            else:
                shown += 1
    except GLib.Error:
        return None
    finally:
        try:
            enumerator.close(None)
        except GLib.Error:
            pass
    return Count(shown, hidden)


def _is_cancelled(error):
    return error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED)
