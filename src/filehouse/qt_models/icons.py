"""Icons and descriptions for content types, cached per type."""

from functools import cache

from gi.repository import Gio
from PySide6.QtGui import QIcon


@cache
def icon_for_content_type(content_type):
    names = list(Gio.content_type_get_icon(content_type).get_names())
    names.append("folder" if content_type == "inode/directory" else "text-x-generic")
    for name in names:
        if QIcon.hasThemeIcon(name):
            return QIcon.fromTheme(name)
    return QIcon.fromTheme("unknown")


@cache
def description_for_content_type(content_type):
    return Gio.content_type_get_description(content_type)
