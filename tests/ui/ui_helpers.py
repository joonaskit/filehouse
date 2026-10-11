"""Small tools for the UI tests."""

from gi.repository import Gio

TIMEOUT = 5000


def gfile(path):
    return Gio.File.new_for_path(str(path))
