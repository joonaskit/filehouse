"""The filters of a file dialog: which files the app that asked wants to be offered.

A filter has a name to show ("Images") and patterns, each a glob for the file's name or a
MIME type. A file passes when any of them fits. Folders are not its business: a dialog
shows them whatever the filter, so that they can be gone into.
"""

from dataclasses import dataclass
from fnmatch import fnmatchcase

from gi.repository import Gio

# The kinds of pattern, as the portal numbers them
GLOB, MIME = 0, 1


@dataclass(frozen=True)
class Filter:
    name: str
    patterns: tuple = ()  # of (GLOB or MIME, text)

    def accepts(self, info):
        """Whether the file with this FileInfo is one of those asked for."""
        return any(_fits(kind, text, info) for kind, text in self.patterns)


def _fits(kind, text, info):
    if kind == GLOB:
        # Whatever the case: "*.jpg" is asked for by apps that mean PHOTO.JPG too
        return fnmatchcase(info.display_name.lower(), text.lower())
    if kind == MIME:
        return _is_type(info.content_type, text)
    return False


def _is_type(content_type, wanted):
    if not content_type or not wanted:
        return False
    family, slash, rest = wanted.partition("/")
    if slash and rest == "*":
        return family == "*" or content_type.startswith(family + "/")
    # A subtype passes for the type it is one of: a text/x-python file is text/plain
    return Gio.content_type_is_a(content_type, wanted)
