"""The one error type file operations report: which file, which step, and why."""

from gi.repository import Gio, GLib

_MESSAGES = {
    Gio.IOErrorEnum.PERMISSION_DENIED: "permission denied",
    Gio.IOErrorEnum.NO_SPACE: "no space left on device",
    Gio.IOErrorEnum.FILENAME_TOO_LONG: "file name too long",
    Gio.IOErrorEnum.NOT_FOUND: "no such file or folder",
    Gio.IOErrorEnum.EXISTS: "already exists",
    Gio.IOErrorEnum.READ_ONLY: "read-only file system",
    Gio.IOErrorEnum.NOT_EMPTY: "folder is not empty",
}


class OperationError(Exception):
    """A step of a file operation failed on one file. Reads as "report.pdf: permission denied"."""

    def __init__(self, path, step, message):
        self.path = path
        self.step = step
        self.message = message
        super().__init__(f"{self.name}: {message}" if path else message)

    @property
    def name(self):
        return self.path.rstrip("/").rpartition("/")[2] or self.path

    @classmethod
    def at(cls, location, step, message):
        return cls(location.get_parse_name(), step, message)

    @classmethod
    def from_glib(cls, error, location, step):
        return cls.at(location, step, readable(error))


def has_code(error, code):
    return error.matches(Gio.io_error_quark(), code)


def is_cancelled(error):
    return has_code(error, Gio.IOErrorEnum.CANCELLED)


def readable(error):
    """A short reason for a `GLib.Error`, without the path Gio puts in its own messages."""
    if error.domain == GLib.quark_to_string(Gio.io_error_quark()):
        try:
            return _MESSAGES.get(Gio.IOErrorEnum(error.code), error.message)
        except ValueError:
            pass
    return error.message
