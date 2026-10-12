"""Locations: turning paths and URIs into Gio files, and naming them.

A location is a `Gio.File`. The UI passes locations around without looking
inside them and asks this module for anything it needs to show.
"""

from dataclasses import dataclass

from gi.repository import Gio, GLib


@dataclass(frozen=True)
class Place:
    name: str
    icon: str
    location: Gio.File


@dataclass(frozen=True)
class StartTarget:
    """Where a command line argument leads: a folder, and maybe a file to select in it."""

    location: Gio.File
    select: str | None = None
    message: str | None = None


def home():
    return Gio.File.new_for_path(GLib.get_home_dir())


def trash():
    """The trash of every drive in one place, as GVFS shows it."""
    return Gio.File.new_for_uri("trash:///")


def has_trash():
    """Whether the trash can be browsed. It can't without GVFS."""
    return "trash" in Gio.Vfs.get_default().get_supported_uri_schemes()


def recent():
    """The files used lately, as GVFS lists them from `recently-used.xbel`."""
    return Gio.File.new_for_uri("recent:///")


def has_recent():
    return "recent" in Gio.Vfs.get_default().get_supported_uri_schemes()


def in_recent(location):
    return location.get_uri_scheme() == "recent"


def entry_location(folder, info):
    """The location of an entry of a listing. In the recent files an entry stands for
    a file somewhere else, and that file is meant."""
    if in_recent(folder) and info.target_uri:
        return Gio.File.new_for_uri(info.target_uri)
    return child(folder, info.name)


def in_trash(location):
    return location.get_uri_scheme() == "trash"


def is_trash(location):
    """Whether this is the trash itself, not something in it."""
    return in_trash(location) and location.get_parent() is None


def from_argument(arg):
    """A location from a command line argument: a path or a URI."""
    return Gio.File.new_for_commandline_arg(arg)


def from_user_input(text):
    """A location from text typed into the path bar. Understands `~`."""
    text = text.strip()
    if text == "~" or text.startswith("~/"):
        return Gio.File.new_for_path(GLib.get_home_dir() + text[1:])
    return Gio.File.new_for_commandline_arg(text)


def same(a, b):
    return a is not None and b is not None and a.equal(b)


def inside(location, folder):
    """Whether `location` lies somewhere under `folder`."""
    return location is not None and folder is not None and location.has_prefix(folder)


def parent(location):
    return location.get_parent()


def child(location, name):
    return location.get_child(name)


def basename(location):
    return location.get_basename()


def name_below(folder, location):
    """The name of `location` as an entry of `folder`: its path below it ("sub/a.txt"
    for a result of a search), or None when it is not in it."""
    return folder.get_relative_path(location)


def uri(location):
    return location.get_uri()


def local_path(location):
    """The path of a location on this computer's own file systems, or None: the
    trash and remote places have none."""
    return location.get_path() if location.is_native() else None


def display_path(location):
    """The full location as editable text: a path when local, a URI otherwise."""
    return location.get_parse_name()


def display_name(location):
    """A short name for titles and breadcrumbs."""
    if location.equal(home()):
        return "Home"
    if is_trash(location):
        return "Trash"
    if in_recent(location) and location.get_parent() is None:
        return "Recent"
    if location.get_parent() is None:
        return "/" if location.is_native() else location.get_uri()
    return location.get_basename() or location.get_uri()


def segments(location):
    """The way to `location` as (name, location), ending with the location itself.

    It starts at the root, or at Home for anything in the home folder: nobody
    needs "/ › home › me" in front of every folder of their own."""
    result = []
    current = location
    home_location = home()
    while current is not None:
        name = "/" if current.get_parent() is None and current.is_native() else None
        if is_trash(current):
            name = "Trash"
        if in_recent(current) and current.get_parent() is None:
            name = "Recent"
        if current.equal(home_location):
            result.append(("Home", current))
            break
        result.append((name or current.get_basename() or current.get_uri(), current))
        current = current.get_parent()
    result.reverse()
    return result


_SPECIAL_DIRS = [
    (GLib.UserDirectory.DIRECTORY_DESKTOP, "user-desktop"),
    (GLib.UserDirectory.DIRECTORY_DOCUMENTS, "folder-documents"),
    (GLib.UserDirectory.DIRECTORY_DOWNLOAD, "folder-download"),
    (GLib.UserDirectory.DIRECTORY_PICTURES, "folder-pictures"),
    (GLib.UserDirectory.DIRECTORY_MUSIC, "folder-music"),
    (GLib.UserDirectory.DIRECTORY_VIDEOS, "folder-videos"),
]


def places():
    """Home and the user's special folders that exist."""
    home_location = home()
    result = [Place("Home", "user-home", home_location)]
    for directory, icon in _SPECIAL_DIRS:
        path = GLib.get_user_special_dir(directory)
        if not path:
            continue
        location = Gio.File.new_for_path(path)
        # An unset special folder is reported as the home folder itself
        if location.equal(home_location) or not location.query_exists(None):
            continue
        result.append(Place(location.get_basename(), icon, location))
    if has_recent():
        result.append(Place("Recent", "document-open-recent", recent()))
    if has_trash():
        result.append(Place("Trash", "user-trash", trash()))
    return result


def resolve_start(arg):
    """Decide what to open for a command line argument.

    A folder opens as itself, a file opens its folder with the file selected,
    and anything that can't be read opens home with a message.
    """
    location = from_argument(arg)
    try:
        info = location.query_info("standard::type", Gio.FileQueryInfoFlags.NONE, None)
    except GLib.Error as error:
        return StartTarget(
            home(), message=f"Could not open {display_path(location)}: {error.message}"
        )
    if info.get_file_type() == Gio.FileType.DIRECTORY:
        return StartTarget(location)
    folder = location.get_parent()
    if folder is None:
        return StartTarget(home(), message=f"Could not open {display_path(location)}")
    return StartTarget(folder, select=location.get_basename())


def resolve_item(arg):
    """Where to show an item in the folder it is in: that folder, with the item selected,
    whether it is a file or a folder. Something with no folder above it opens as itself."""
    location = from_argument(arg)
    folder = location.get_parent()
    if folder is None:
        return StartTarget(location)
    return StartTarget(folder, select=location.get_basename())


def file_type(location, cancellable=None):
    """What is at `location`, links not followed: a `Gio.FileType`, or None when nothing is."""
    try:
        info = location.query_info(
            "standard::type", Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, cancellable
        )
    except GLib.Error as error:
        if error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_FOUND):
            return None
        raise
    return info.get_file_type()


def from_path(path):
    return Gio.File.new_for_path(path)


def is_folder(location):
    """Whether `location` is a folder, or a link to one."""
    try:
        info = location.query_info("standard::type", Gio.FileQueryInfoFlags.NONE, None)
    except GLib.Error:
        return False
    return info.get_file_type() == Gio.FileType.DIRECTORY


def from_uri(uri):
    return Gio.File.new_for_uri(uri)


def nearest_folder(location):
    """`location` when it is a folder, or else the nearest folder above it that is
    there: for a place remembered from last time. None when nothing of it is left."""
    while location is not None and not is_folder(location):
        location = location.get_parent()
    return location


def same_filesystem(a, b):
    """Whether a move from one to the other would be a rename. False when it can't be told."""
    try:
        ids = [
            location.query_info(
                "id::filesystem", Gio.FileQueryInfoFlags.NONE, None
            ).get_attribute_string("id::filesystem")
            for location in (a, b)
        ]
    except GLib.Error:
        return False
    return ids[0] is not None and ids[0] == ids[1]
