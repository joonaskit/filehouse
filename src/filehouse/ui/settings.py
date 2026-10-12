"""What the app remembers between runs: window size, view mode, hidden files, the split
and whether opened files are remembered."""

from PySide6.QtCore import QSettings, QSize

from filehouse import APP_ID

DETAILS, ICONS = "details", "icons"


def _store():
    # $XDG_CONFIG_HOME/filehouse/filehouse.conf
    return QSettings(APP_ID, APP_ID)


def window_size(default):
    size = _store().value("window/size")
    return size if isinstance(size, QSize) and size.isValid() else default


def set_window_size(size):
    _store().setValue("window/size", size)


def view_mode():
    mode = _store().value("view/mode", DETAILS)
    return mode if mode in (DETAILS, ICONS) else DETAILS


def set_view_mode(mode):
    _store().setValue("view/mode", mode)


def show_hidden():
    return _store().value("view/show_hidden", False, type=bool)


def set_show_hidden(show):
    _store().setValue("view/show_hidden", bool(show))


def split():
    return _store().value("view/split", False, type=bool)


def set_split(split):
    _store().setValue("view/split", bool(split))


def remember_opened():
    """Whether files opened from the app are told to the desktop's lists of recent files."""
    return _store().value("privacy/remember_opened", True, type=bool)


def set_remember_opened(remember):
    _store().setValue("privacy/remember_opened", bool(remember))


def clear():
    _store().clear()
