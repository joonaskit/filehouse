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


def parent(location):
    return location.get_parent()


def child(location, name):
    return location.get_child(name)


def basename(location):
    return location.get_basename()


def uri(location):
    return location.get_uri()


def display_path(location):
    """The full location as editable text: a path when local, a URI otherwise."""
    return location.get_parse_name()


def display_name(location):
    """A short name for titles and breadcrumbs."""
    if location.equal(home()):
        return "Home"
    if location.get_parent() is None:
        return "/" if location.is_native() else location.get_uri()
    return location.get_basename() or location.get_uri()


def segments(location):
    """Every ancestor of `location` and the location itself, root first, as (name, location)."""
    result = []
    current = location
    while current is not None:
        name = "/" if current.get_parent() is None and current.is_native() else None
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


def from_uri(uri):
    return Gio.File.new_for_uri(uri)


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
