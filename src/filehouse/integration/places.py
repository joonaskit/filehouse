"""The user's own places, in the file Dolphin and the file dialogs of KDE share:
`$XDG_DATA_HOME/user-places.xbel`.

Plain XBEL with KDE's own notes on every entry (an ID, whether it is a system item,
whether it is hidden) and some entries that are not bookmarks at all (the separators
that keep a drive's place). A reader that knew only bookmarks, `GBookmarkFile`, would
drop all of that when it wrote the file again, so the file is edited as XML and
everything that is not touched is written back as it was.

The places that are the user's are the bookmarks that are not system items (Home, the
trash, the network, the recent files and the like) and not hidden.

If the file is not there, nothing is written: Dolphin makes it with the system items on
its first run, and a file made here would be taken for a finished one.
"""

import logging
import os
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from gi.repository import GLib

log = logging.getLogger(__name__)

_FREEDESKTOP = "http://www.freedesktop.org/standards/desktop-bookmarks"
_KDE = "http://www.kde.org"
_OWNER_FREEDESKTOP = "http://freedesktop.org"

_NAMESPACES = (
    ("bookmark", _FREEDESKTOP),
    ("kdepriv", "http://www.kde.org/kdepriv"),
    ("mime", "http://www.freedesktop.org/standards/shared-mime-info"),
)
for _prefix, _uri in _NAMESPACES:
    ET.register_namespace(_prefix, _uri)

_ICON = f"{{{_FREEDESKTOP}}}icon"
DEFAULT_ICON = "folder"


@dataclass(frozen=True)
class Place:
    id: str
    uri: str
    title: str
    icon: str


def default_path():
    return os.path.join(GLib.get_user_data_dir(), "user-places.xbel")


def _kde_metadata(bookmark):
    for metadata in bookmark.iterfind("info/metadata"):
        if metadata.get("owner") == _KDE:
            return metadata
    return None


def _kde_text(bookmark, name):
    metadata = _kde_metadata(bookmark)
    found = metadata.find(name) if metadata is not None else None
    return (found.text or "") if found is not None else ""


def _is_users(bookmark):
    return (
        bool(bookmark.get("href"))
        and _kde_text(bookmark, "isSystemItem") != "true"
        and _kde_text(bookmark, "IsHidden") != "true"
    )


def _place(bookmark):
    icon = bookmark.find(f"info/metadata/{_ICON}")
    return Place(
        id=_kde_text(bookmark, "ID"),
        uri=bookmark.get("href"),
        title=(bookmark.findtext("title") or "").strip(),
        icon=icon.get("name") if icon is not None and icon.get("name") else DEFAULT_ICON,
    )


class PlacesFile:
    def __init__(self, path=None):
        self.path = path or default_path()

    @property
    def exists(self):
        return os.path.isfile(self.path)

    def read(self):
        """The user's places in the order of the file, or None when the file is not there
        or cannot be read."""
        root = self._load()
        if root is None:
            return None
        return [_place(bookmark) for bookmark in root.iterfind("bookmark") if _is_users(bookmark)]

    # Changes. Each reads the file, changes it and writes it again; whether it did

    def add(self, uri, title, icon=DEFAULT_ICON):
        """Add a place at the end of the user's, unless there is one for the uri."""
        root = self._load()
        if root is None:
            return False
        users = [b for b in root.iterfind("bookmark") if _is_users(b)]
        if any(b.get("href") == uri for b in users):
            return False
        bookmark = ET.Element("bookmark", {"href": uri})
        ET.SubElement(bookmark, "title").text = title
        info = ET.SubElement(bookmark, "info")
        shared = ET.SubElement(info, "metadata", {"owner": _OWNER_FREEDESKTOP})
        ET.SubElement(shared, _ICON, {"name": icon})
        kde = ET.SubElement(info, "metadata", {"owner": _KDE})
        ET.SubElement(kde, "ID").text = self._new_id(root)
        ET.SubElement(kde, "isSystemItem").text = "false"
        # After the last bookmark, so before the separators and whatever else follows
        last = max((i for i, el in enumerate(root) if el.tag == "bookmark"), default=-1)
        root.insert(last + 1, bookmark)
        return self._save(root)

    def remove(self, place_id):
        bookmark = self._find(root := self._load(), place_id)
        if bookmark is None:
            return False
        root.remove(bookmark)
        return self._save(root)

    def rename(self, place_id, title):
        bookmark = self._find(root := self._load(), place_id)
        if bookmark is None:
            return False
        element = bookmark.find("title")
        if element is None:
            element = ET.Element("title")
            bookmark.insert(0, element)
        element.text = title
        return self._save(root)

    def move(self, place_id, offset):
        """Move a place past the next (offset 1) or the previous (-1) of the user's."""
        bookmark = self._find(root := self._load(), place_id)
        if bookmark is None:
            return False
        users = [b for b in root.iterfind("bookmark") if _is_users(b)]
        target = users.index(bookmark) + offset
        if not 0 <= target < len(users):
            return False
        other = users[target]
        children = list(root)
        one, two = children.index(bookmark), children.index(other)
        children[one], children[two] = other, bookmark
        root[:] = children
        return self._save(root)

    # The file

    @staticmethod
    def _find(root, place_id):
        if root is None or not place_id:
            return None
        return next(
            (
                b
                for b in root.iterfind("bookmark")
                if _is_users(b) and _kde_text(b, "ID") == place_id
            ),
            None,
        )

    @staticmethod
    def _new_id(root):
        """As KDE makes them: the time, and a number one above the highest there is."""
        numbers = []
        for bookmark in root.iterfind("bookmark"):
            _, _, number = _kde_text(bookmark, "ID").partition("/")
            if number.isdigit():
                numbers.append(int(number))
        return f"{int(time.time())}/{max(numbers, default=-1) + 1}"

    def _load(self):
        try:
            root = ET.parse(self.path).getroot()
        except (OSError, ET.ParseError) as error:
            log.debug("could not read %s: %s", self.path, error)
            return None
        return root if root.tag == "xbel" else None

    def _save(self, root):
        ET.indent(root, space=" ")
        # As KDE writes it: empty elements without a space, and the namespaces it declares
        # even where nothing in the file uses them
        body = ET.tostring(root, encoding="unicode").replace(" />", "/>")
        for prefix, uri in _NAMESPACES:
            if f"xmlns:{prefix}=" not in body.partition(">")[0]:
                body = body.replace("<xbel", f'<xbel xmlns:{prefix}="{uri}"', 1)
        text = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xbel>\n' + body + "\n"
        folder = os.path.dirname(self.path)
        try:
            mode = os.stat(self.path).st_mode & 0o777
            # Written under another name and moved into place, so nobody reads half a file
            handle, temporary = tempfile.mkstemp(dir=folder, prefix=".user-places-")
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as f:
                    f.write(text)
                os.chmod(temporary, mode)
                os.replace(temporary, self.path)
            except BaseException:
                os.unlink(temporary)
                raise
        except OSError as error:
            log.debug("could not write %s: %s", self.path, error)
            return False
        return True
