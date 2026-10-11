"""The two operations that finish at once and need no job: new folder and rename.

They report failure with the same OperationError as jobs do.
"""

from gi.repository import GLib

from filehouse.core import locations
from filehouse.core.errors import OperationError
from filehouse.core.operations.conflicts import free_name

_NEW_FOLDER = "New folder"


def suggest_folder_name(folder):
    """ "New folder", or "New folder (2)" and so on when that is taken."""
    try:
        if locations.file_type(folder.get_child(_NEW_FOLDER)) is None:
            return _NEW_FOLDER
        return free_name(folder, _NEW_FOLDER, is_dir=True)
    except GLib.Error:
        return _NEW_FOLDER


def create_folder(folder, name):
    """Make the folder `name` in `folder` and return it. Never replaces anything."""
    name = _checked(name, "create folder")
    location = folder.get_child(name)
    try:
        location.make_directory(None)
    except GLib.Error as error:
        raise OperationError.from_glib(error, location, "create folder") from error
    return location


def rename(location, name):
    """Give `location` a new name in the same folder and return where it is now.
    Never replaces anything."""
    name = _checked(name, "rename")
    if name == location.get_basename():
        return location
    try:
        return location.set_display_name(name, None)
    except GLib.Error as error:
        raise OperationError.from_glib(error, location, "rename") from error


def _checked(name, step):
    name = name.strip()
    if not name:
        raise OperationError("", step, "a name is needed")
    if "/" in name:
        raise OperationError("", step, "a name cannot contain “/”")
    if name in (".", ".."):
        raise OperationError("", step, f"“{name}” cannot be used as a name")
    return name
