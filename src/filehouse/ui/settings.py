"""What the app remembers between runs: window size, view mode, hidden files, the split,
the density, the notifications and whether opened files are remembered."""

from PySide6.QtCore import QObject, QSettings, QSize, Signal

from filehouse import APP_ID

DETAILS, ICONS = "details", "icons"


class _Changes(QObject):
    # A setting that is shown in every window changed
    remember_opened_changed = Signal(bool)


changes = _Changes()


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


def density():
    """How much room the lists and the sidebar take; read at the start."""
    value = _store().value("view/density", "comfortable")
    return value if value in ("comfortable", "compact") else "comfortable"


def set_density(density):
    _store().setValue("view/density", density)


def notify():
    """Whether a job that ends with no window showing is told in a notification."""
    return _store().value("notifications/enabled", True, type=bool)


def set_notify(notify):
    _store().setValue("notifications/enabled", bool(notify))


def long_job_seconds():
    """With the island showing jobs, how long a job runs before it is worth a notification."""
    return max(1, _store().value("notifications/long_job_seconds", 10, type=int))


def set_long_job_seconds(seconds):
    _store().setValue("notifications/long_job_seconds", max(1, int(seconds)))


def split():
    return _store().value("view/split", False, type=bool)


def set_split(split):
    _store().setValue("view/split", bool(split))


def remember_opened():
    """Whether files opened from the app are told to the desktop's lists of recent files."""
    return _store().value("privacy/remember_opened", True, type=bool)


def set_remember_opened(remember):
    _store().setValue("privacy/remember_opened", bool(remember))
    changes.remember_opened_changed.emit(bool(remember))


def collapsed_groups():
    """The groups of the sidebar that are folded shut, by key."""
    value = _store().value("sidebar/collapsed", [])
    if isinstance(value, str):
        value = [value]
    return [str(key) for key in value or []]


def set_group_collapsed(key, collapsed):
    groups = [group for group in collapsed_groups() if group != key]
    if collapsed:
        groups.append(key)
    _store().setValue("sidebar/collapsed", groups)


def clear():
    _store().clear()
