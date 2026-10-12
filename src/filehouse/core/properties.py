"""What the properties dialog shows about a file: its details, and the size of a folder."""

from dataclasses import dataclass, replace

from gi.repository import Gio, GLib

from filehouse.core.errors import OperationError, is_cancelled

_ATTRIBUTES = ",".join(
    [
        "standard::name",
        "standard::display-name",
        "standard::type",
        "standard::size",
        "standard::content-type",
        "standard::is-symlink",
        "standard::symlink-target",
        "time::modified",
        "time::access",
        "time::created",
        "unix::mode",
        "owner::user",
        "owner::group",
        "trash::orig-path",
    ]
)
_WALK_ATTRIBUTES = "standard::name,standard::type,standard::size"
_NOFOLLOW = Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS
# How many entries are counted between two progress reports
_REPORT_EVERY = 500


@dataclass(frozen=True)
class Properties:
    name: str
    folder: str  # the folder it is in, as text
    kind: str  # "Folder", "PDF document", …
    content_type: str
    is_dir: bool
    link_target: str | None
    size: int
    # Seconds since the epoch, 0 when unknown
    modified: int
    accessed: int
    created: int
    mode: int | None  # permission bits, None where there are none (not a Unix file system)
    owner: str
    group: str
    original_path: str | None  # where a trashed item was


@dataclass(frozen=True)
class Totals:
    """What a folder holds, all the way down."""

    size: int = 0
    files: int = 0
    folders: int = 0
    unreadable: int = 0  # folders that could not be looked into, so the rest is a minimum


def read(location):
    """The properties of one file, a link not followed. Raises OperationError."""
    try:
        info = location.query_info(_ATTRIBUTES, _NOFOLLOW, None)
    except GLib.Error as error:
        raise OperationError.from_glib(error, location, "read") from error
    parent = location.get_parent()
    content_type = info.get_content_type()
    mode = None
    if info.has_attribute("unix::mode"):
        mode = info.get_attribute_uint32("unix::mode") & 0o7777
    return Properties(
        name=info.get_display_name(),
        folder=parent.get_parse_name() if parent is not None else "",
        kind=Gio.content_type_get_description(content_type) if content_type else "",
        content_type=content_type or "application/octet-stream",
        is_dir=info.get_file_type() == Gio.FileType.DIRECTORY,
        link_target=info.get_symlink_target() if info.get_is_symlink() else None,
        size=info.get_size(),
        modified=info.get_attribute_uint64("time::modified"),
        accessed=info.get_attribute_uint64("time::access"),
        created=info.get_attribute_uint64("time::created"),
        mode=mode,
        owner=info.get_attribute_string("owner::user") or "",
        group=info.get_attribute_string("owner::group") or "",
        original_path=info.get_attribute_byte_string("trash::orig-path"),
    )


def measure(roots, cancellable=None, on_progress=None):
    """Add up everything in and under `roots`, links not followed. Blocks: for a worker thread.

    `on_progress(Totals)` is called now and then with the running totals. Returns the
    final Totals, or None when cancelled.
    """
    totals = Totals()
    pending = []
    seen = 0
    try:
        for root in roots:
            info = root.query_info(_WALK_ATTRIBUTES, _NOFOLLOW, cancellable)
            totals = _add(totals, info, root, pending)
        while pending:
            folder = pending.pop()
            try:
                enumerator = folder.enumerate_children(_WALK_ATTRIBUTES, _NOFOLLOW, cancellable)
            except GLib.Error as error:
                if is_cancelled(error):
                    return None
                totals = replace(totals, unreadable=totals.unreadable + 1)
                continue
            try:
                while (info := enumerator.next_file(cancellable)) is not None:
                    totals = _add(totals, info, folder.get_child(info.get_name()), pending)
                    seen += 1
                    if on_progress is not None and seen % _REPORT_EVERY == 0:
                        on_progress(totals)
            finally:
                try:
                    enumerator.close(None)
                except GLib.Error:
                    pass
    except GLib.Error as error:
        if is_cancelled(error):
            return None
        totals = replace(totals, unreadable=totals.unreadable + 1)
    return totals


def _add(totals, info, location, pending):
    if info.get_file_type() == Gio.FileType.DIRECTORY:
        pending.append(location)
        return replace(totals, folders=totals.folders + 1)
    size = info.get_size() if info.get_file_type() == Gio.FileType.REGULAR else 0
    return replace(totals, size=totals.size + size, files=totals.files + 1)


def permissions(mode, is_dir):
    """What owner, group and others may do, as three short texts."""
    words = ("read", "write", "open" if is_dir else "run")
    result = []
    for shift in (6, 3, 0):
        bits = (mode >> shift) & 0o7
        allowed = [word for bit, word in zip((4, 2, 1), words, strict=True) if bits & bit]
        result.append(", ".join(allowed).capitalize() if allowed else "Nothing")
    return tuple(result)
