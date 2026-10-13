"""Finding files by name in the folders below one.

The entries of the folder itself are listed already, and the list filters them. This
walks what is below: every folder under the root, breadth first, so what is near is
found first. A result is a `FileInfo` whose `name` is its path below the root
("sub/deep/report.pdf"), which is what makes it one entry of the root's listing like
any other: `root.get_child(name)` is the file.
"""

import fnmatch
import time
from collections import deque
from dataclasses import replace

from gi.repository import Gio, GLib

from filehouse.core import locations
from filehouse.core.listing import ATTRIBUTES, info_from_gio

# A search that finds more than this is too wide to read: it stops and says so
MAX_RESULTS = 5000
_BATCH_SIZE = 200
# Results are handed over at least this often, so the first ones show at once
_BATCH_SECONDS = 0.15
# Not files: the kernel's and the session's own trees
_NOT_SEARCHED = ("/proc", "/sys", "/dev", "/run")


def words(text):
    """What a text asks for: its words, in any order and any case."""
    return text.casefold().split()


def matches(wanted, name):
    """Whether a name has every one of the words in it."""
    name = name.casefold()
    return all(word in name for word in wanted)


def selector(text):
    """What a text typed to select by picks, as a function of a name. With a "*", a "?"
    or a "[" it is a pattern for the whole name ("*.jpg"); without one it is the
    search's words. Any case, and an empty text picks nothing."""
    text = text.strip()
    if any(sign in text for sign in "*?["):
        pattern = text.casefold()
        return lambda name: fnmatch.fnmatchcase(name.casefold(), pattern)
    wanted = words(text)
    return lambda name: bool(wanted) and matches(wanted, name)


def can_search_below(location):
    """The trash and the recent files have nothing below them to search."""
    return not locations.in_trash(location) and not locations.in_recent(location)


def search_below(
    root,
    text,
    on_batch,
    cancellable=None,
    show_hidden=False,
    limit=MAX_RESULTS,
    batch_seconds=_BATCH_SECONDS,
    include_root=False,
):
    """Find what matches `text` in the folders below `root`, calling
    `on_batch(list[FileInfo])` as results arrive. The entries of `root` itself are not
    results, unless `include_root` asks for them: a list of the folder filters those
    itself, and a caller without one has nothing that would. Hidden files and folders
    are searched only with `show_hidden`.

    Blocks, so call it from a worker thread. Links are never followed, so it ends. A
    folder that can't be read is passed over. Returns whether it stopped at `limit`,
    and returns quietly when cancelled.
    """
    wanted = words(text)
    if not wanted:
        return False
    found = 0
    batch = []
    handed = time.monotonic()
    folders = deque([(root, "")])
    while folders:
        folder, prefix = folders.popleft()
        if cancellable is not None and cancellable.is_cancelled():
            return False
        try:
            enumerator = folder.enumerate_children(
                ATTRIBUTES, Gio.FileQueryInfoFlags.NONE, cancellable
            )
        except GLib.Error:
            continue
        try:
            while (raw := enumerator.next_file(cancellable)) is not None:
                if not show_hidden and (raw.get_is_hidden() or raw.get_is_backup()):
                    continue
                name = raw.get_name()
                path = f"{prefix}{name}"
                is_folder = raw.get_file_type() == Gio.FileType.DIRECTORY
                # A link to a folder is listed, and not gone into: it may lead back here
                if is_folder and not raw.get_is_symlink() and not _not_searched(folder, name):
                    folders.append((folder.get_child(name), f"{path}/"))
                if (prefix or include_root) and matches(wanted, raw.get_display_name()):
                    batch.append(replace(info_from_gio(raw), name=path))
                    found += 1
                    if found >= limit:
                        on_batch(batch)
                        return True
                if batch and (
                    len(batch) >= _BATCH_SIZE or time.monotonic() - handed >= batch_seconds
                ):
                    on_batch(batch)
                    batch = []
                    handed = time.monotonic()
        except GLib.Error:
            # Cancelled, or the folder went away: the next turn of the loop tells which
            pass
        finally:
            try:
                enumerator.close(None)
            except GLib.Error:
                pass
    if cancellable is not None and cancellable.is_cancelled():
        return False
    if batch:
        on_batch(batch)
    return False


def _not_searched(folder, name):
    path = folder.get_path()
    return path is not None and f"{path.rstrip('/')}/{name}" in _NOT_SEARCHED
