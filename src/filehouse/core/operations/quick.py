"""The operations that finish at once and need no job: new folder, new file, rename
and making a link.

They report failure with the same OperationError as jobs do.
"""

from gi.repository import Gio, GLib

from filehouse.core import locations
from filehouse.core.errors import OperationError, has_code
from filehouse.core.operations.conflicts import free_name

# What a new folder or file is called until it is named, when that is free
NEW_FOLDER = "New folder"
NEW_FILE = "New file"


def suggest_folder_name(folder):
    """ "New folder", or "New folder (2)" and so on when that is taken."""
    try:
        if locations.file_type(folder.get_child(NEW_FOLDER)) is None:
            return NEW_FOLDER
        return free_name(folder, NEW_FOLDER, is_dir=True)
    except GLib.Error:
        return NEW_FOLDER


def create_folder(folder, name):
    """Make the folder `name` in `folder` and return it. Never replaces anything."""
    name = checked_name(name, "create folder")
    location = folder.get_child(name)
    try:
        location.make_directory(None)
    except GLib.Error as error:
        raise OperationError.from_glib(error, location, "create folder") from error
    return location


def suggest_file_name(folder):
    """ "New file", or "New file (2)" and so on when that is taken."""
    try:
        if locations.file_type(folder.get_child(NEW_FILE)) is None:
            return NEW_FILE
        return free_name(folder, NEW_FILE, is_dir=False)
    except GLib.Error:
        return NEW_FILE


def create_file(folder, name):
    """Make the empty file `name` in `folder` and return it. Never replaces anything."""
    name = checked_name(name, "create file")
    location = folder.get_child(name)
    try:
        # Without REPLACE_DESTINATION this fails when something has the name already
        location.create(Gio.FileCreateFlags.NONE, None).close(None)
    except GLib.Error as error:
        raise OperationError.from_glib(error, location, "create file") from error
    return location


def rename(location, name):
    """Give `location` a new name in the same folder and return where it is now.
    Never replaces anything."""
    name = checked_name(name, "rename")
    if name == location.get_basename():
        return location
    try:
        return location.set_display_name(name, None)
    except GLib.Error as error:
        raise OperationError.from_glib(error, location, "rename") from error


def link_name(name):
    """What a link is called where the name of what it points to is taken."""
    return f"Link to {name}"


def create_link(target, folder):
    """Make a symbolic link to `target` in `folder` and return it. Never replaces anything.

    Beside its target the link is relative (the target's name), so the two can be moved
    together; in another folder it is the target's full path, so the link can be moved.
    It has the target's name, or "Link to …" where that is taken."""
    path = target.get_path()
    if path is None or not target.is_native():
        raise OperationError.at(target, "link", "a link can only point to this computer")
    name = target.get_basename()
    beside = locations.same(target.get_parent(), folder)
    points_to = name if beside else path
    candidate = link_name(name) if beside else name
    try:
        is_dir = locations.file_type(target) == Gio.FileType.DIRECTORY
        while True:
            link = folder.get_child(candidate)
            try:
                link.make_symbolic_link(points_to, None)
                return link
            except GLib.Error as error:
                if not has_code(error, Gio.IOErrorEnum.EXISTS):
                    raise
            if candidate == name:
                candidate = link_name(name)
            else:
                candidate = free_name(folder, link_name(name), is_dir)
    except GLib.Error as error:
        raise OperationError.from_glib(error, target, "link") from error


def create_links(targets, folder):
    """A link in `folder` to each of `targets`. Returns the links made and an
    OperationError for each that could not be: it carries on after a failure."""
    made, errors = [], []
    for target in targets:
        try:
            made.append(create_link(target, folder))
        except OperationError as error:
            errors.append(error)
    return made, errors


def checked_name(name, step):
    """`name` as it can be given to a file, or an OperationError saying why it cannot."""
    name = name.strip()
    if not name:
        raise OperationError("", step, "a name is needed")
    if "/" in name:
        raise OperationError("", step, "a name cannot contain “/”")
    if name in (".", ".."):
        raise OperationError("", step, f"“{name}” cannot be used as a name")
    return name
