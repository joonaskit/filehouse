"""Thumbnails, shared with other file managers through the freedesktop cache.

A thumbnail is looked up in `$XDG_CACHE_HOME/thumbnails/normal/<md5 of the URI>.png`
and used when its `Thumb::MTime` matches the file, whatever the file's type: this is
how what Dolphin has made for videos and PDFs is shown. Otherwise it is made here
and written back the way the thumbnail spec asks, so the others can use it too:

- images Qt can read, by Qt itself;
- everything else by the desktop's thumbnailers, the programs described in
  `thumbnailers/*.thumbnailer` in the data directories. They are other people's
  programs run on any file, so they are few at a time, each has a time limit, and
  a file one failed on is remembered in the spec's `fail` folder and not tried again.
"""

import configparser
import hashlib
import os
import shlex
import shutil
import signal
import tempfile
from functools import cache

from PySide6.QtCore import (
    QObject,
    QProcess,
    QRunnable,
    QSize,
    QStandardPaths,
    Qt,
    QThreadPool,
    Signal,
)
from PySide6.QtGui import QImage, QImageReader

from filehouse.qt_models.workers import Outbox

# The spec's "normal" size
SIZE = 128
# Decoding a huge image for a small picture is not worth the wait
_MAX_BYTES = 64 * 1024 * 1024
# A file above this is not handed to a thumbnailer
_MAX_EXTERNAL_BYTES = 4 * 1024**3
# A thumbnailer that takes longer than this, in seconds, is stopped and counts as failed
_EXTERNAL_SECONDS = 15
# How many run at once
_EXTERNAL_AT_ONCE = 2
# How long a program may take to start, in milliseconds
_START_MS = 5000
# What the spec's `fail` folder is called for this program
_FAIL_NAME = "filehouse"

_supported = None


def can_thumbnail(content_type):
    global _supported
    if _supported is None:
        _supported = {bytes(name).decode() for name in QImageReader.supportedMimeTypes()}
    return content_type in _supported


def cache_dir():
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.GenericCacheLocation)
    return os.path.join(base, "thumbnails", "normal")


def cache_path(uri):
    return os.path.join(cache_dir(), hashlib.md5(uri.encode()).hexdigest() + ".png")


def fail_path(uri):
    base = os.path.dirname(cache_dir())
    return os.path.join(base, "fail", _FAIL_NAME, hashlib.md5(uri.encode()).hexdigest() + ".png")


def failed(uri, mtime):
    """Whether a thumbnail of this version of the file was tried and could not be made."""
    marker = QImage(fail_path(uri))
    return not marker.isNull() and marker.text("Thumb::MTime") == str(mtime)


def _mark_failed(uri, mtime):
    marker = QImage(1, 1, QImage.Format.Format_ARGB32)
    marker.fill(Qt.GlobalColor.transparent)
    marker.setText("Thumb::URI", uri)
    marker.setText("Thumb::MTime", str(mtime))
    marker.setText("Software", "filehouse")
    path = fail_path(uri)
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        marker.save(path, "PNG")
    except OSError:
        pass


def load(uri, mtime):
    """The cached thumbnail of a file, or None when there is none or the file has changed."""
    # The image's own text, not the reader's: the reader trips over the "::" in the keys
    image = QImage(cache_path(uri))
    if image.isNull() or image.text("Thumb::MTime") != str(mtime):
        return None
    return image


def make(uri, path, mtime, size):
    """Make the thumbnail of an image file and put it in the cache. None when it can't be made."""
    if size > _MAX_BYTES or os.path.dirname(path) == cache_dir():
        return None
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    full = reader.size()
    if full.isValid() and (full.width() > SIZE or full.height() > SIZE):
        # Scaled while decoding, which is much cheaper for a large JPEG
        reader.setScaledSize(full.scaled(QSize(SIZE, SIZE), Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        _mark_failed(uri, mtime)
        return None
    if image.width() > SIZE or image.height() > SIZE:
        image = image.scaled(
            SIZE,
            SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    _store(image, uri, mtime, size, full)
    return image


# The desktop's thumbnailers


class DesktopThumbnailer:
    """One `.thumbnailer` file: the command, and the types it is for."""

    def __init__(self, name, command, mime_types):
        self.name = name
        self.command = command
        self.mime_types = mime_types


def _data_dirs():
    home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    others = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    return [home, *[path for path in others.split(":") if path]]


@cache
def _registry():
    """Mime type -> the thumbnailer for it, from the data directories (the user's first)."""
    found = {}
    seen = set()
    for directory in _data_dirs():
        folder = os.path.join(directory, "thumbnailers")
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            continue
        for name in names:
            if not name.endswith(".thumbnailer") or name in seen:
                continue
            seen.add(name)
            entry = _read_entry(name, os.path.join(folder, name))
            for mime_type in entry.mime_types if entry else ():
                found.setdefault(mime_type, entry)
    return found


def _read_entry(name, path):
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    try:
        parser.read(path, encoding="utf-8")
        section = parser["Thumbnailer Entry"]
        command = shlex.split(section["Exec"])
        mime_types = [part for part in section.get("MimeType", "").split(";") if part]
    except (OSError, UnicodeDecodeError, configparser.Error, KeyError, ValueError):
        return None
    try_exec = section.get("TryExec")
    if not command or not mime_types or (try_exec and shutil.which(try_exec) is None):
        return None
    if shutil.which(command[0]) is None:
        return None
    return DesktopThumbnailer(name, command, mime_types)


def reload_thumbnailers():
    _registry.cache_clear()


def thumbnailer_for(content_type):
    """The desktop's thumbnailer for a type, or None."""
    return _registry().get(content_type)


def _expand(command, path, uri, output, size):
    """The command with the spec's placeholders filled in: %i the file, %u its URI,
    %o where to write, %s the size, %% a percent sign."""
    values = {"i": path, "u": uri, "o": output, "s": str(size), "%": "%"}
    expanded = []
    for token in command:
        text, index = "", 0
        while index < len(token):
            if token[index] == "%" and index + 1 < len(token) and token[index + 1] in values:
                text += values[token[index + 1]]
                index += 2
            else:
                text += token[index]
                index += 1
        expanded.append(text)
    return expanded


def make_with_thumbnailer(thumbnailer, uri, path, mtime, size):
    """Make the thumbnail of a file with the desktop's thumbnailer for its type and put it
    in the cache. None when it can't be made; the failure is remembered."""
    if size > _MAX_EXTERNAL_BYTES or os.path.dirname(path) == cache_dir():
        return None
    image = None
    with tempfile.TemporaryDirectory(prefix="filehouse-thumb-") as folder:
        output = os.path.join(folder, "thumbnail.png")
        command = _expand(thumbnailer.command, path, uri, output, SIZE)
        if _run(command, folder):
            image = QImage(output)
    if image is None or image.isNull():
        _mark_failed(uri, mtime)
        return None
    if image.width() > SIZE or image.height() > SIZE:
        image = image.scaled(
            SIZE,
            SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    _store(image, uri, mtime, size, QSize())
    return image


def _run(command, folder):
    """Run a command in `folder`, for at most `_EXTERNAL_SECONDS`. Whether it succeeded.

    With QProcess, used blocking on this worker thread: Python's `subprocess` forking
    from one of Qt's pool threads brought the whole process down now and then."""
    process = QProcess()
    process.setWorkingDirectory(folder)
    process.setProgram(command[0])
    process.setArguments(command[1:])
    process.setStandardInputFile(QProcess.nullDevice())
    process.setStandardOutputFile(QProcess.nullDevice())
    process.setStandardErrorFile(QProcess.nullDevice())
    # Its own session, so what it started stops with it
    process.setUnixProcessParameters(QProcess.UnixProcessFlag.CreateNewSession)
    process.start()
    if not process.waitForStarted(_START_MS):
        return False
    if not process.waitForFinished(round(_EXTERNAL_SECONDS * 1000)):
        try:
            os.killpg(process.processId(), signal.SIGKILL)
        except OSError:
            process.kill()
        process.waitForFinished(_START_MS)
        return False
    return process.exitStatus() == QProcess.ExitStatus.NormalExit and process.exitCode() == 0


def _store(image, uri, mtime, size, full):
    image.setText("Thumb::URI", uri)
    image.setText("Thumb::MTime", str(mtime))
    image.setText("Thumb::Size", str(size))
    if full.isValid():
        image.setText("Thumb::Image::Width", str(full.width()))
        image.setText("Thumb::Image::Height", str(full.height()))
    image.setText("Software", "filehouse")
    folder = cache_dir()
    try:
        os.makedirs(folder, mode=0o700, exist_ok=True)
        # Written under another name and moved into place, so nobody reads half a file
        handle, temp = tempfile.mkstemp(suffix=".png", dir=folder)
        os.close(handle)
        if image.save(temp, "PNG"):
            os.chmod(temp, 0o600)
            os.replace(temp, cache_path(uri))
        else:
            os.unlink(temp)
    except OSError:
        # A cache that can't be written only costs time
        pass


# A pool of its own: a few programs at a time, and never all the threads of the app
_external_pool = QThreadPool()
_external_pool.setMaxThreadCount(_EXTERNAL_AT_ONCE)


class _Wanted:
    """Which round of asking a Thumbnailer is in: what was asked for in an earlier one
    is not made. A plain object, read on the worker threads, where the Thumbnailer
    itself may be gone."""

    def __init__(self):
        self.round = 0


class _Task(QRunnable):
    """Looks in the cache, then makes the thumbnail if Qt can. What only a thumbnailer
    can make is handed over to the pool for those."""

    def __init__(self, token, uri, path, mtime, size, content_type, outbox, wanted):
        super().__init__()
        self._arguments = (uri, path, mtime, size)
        self._content_type = content_type
        self._token = token
        self._outbox = outbox
        self._wanted = (wanted, wanted.round)

    def run(self):
        if not _still_wanted(self._wanted):
            return
        uri, path, mtime, size = self._arguments
        image = load(uri, mtime)
        if image is None and not failed(uri, mtime):
            if can_thumbnail(self._content_type):
                image = make(uri, path, mtime, size)
            else:
                thumbnailer = thumbnailer_for(self._content_type)
                if thumbnailer is not None:
                    _external_pool.start(
                        _ExternalTask(
                            self._token, thumbnailer, self._arguments, self._outbox, self._wanted
                        )
                    )
                    return
        _answer(self._outbox, self._token, image)


class _ExternalTask(QRunnable):
    def __init__(self, token, thumbnailer, arguments, outbox, wanted):
        super().__init__()
        self._thumbnailer = thumbnailer
        self._arguments = arguments
        self._token = token
        self._outbox = outbox
        self._wanted = wanted

    def run(self):
        if not _still_wanted(self._wanted):
            return
        uri, path, mtime, size = self._arguments
        image = make_with_thumbnailer(self._thumbnailer, uri, path, mtime, size)
        _answer(self._outbox, self._token, image)


def _still_wanted(wanted):
    holder, round_ = wanted
    return holder.round == round_


def _answer(outbox, token, image):
    # The image is null when there is no thumbnail to show
    outbox.send("ready", token, image or QImage())


class Thumbnailer(QObject):
    """Loads or makes thumbnails on worker threads. `ready(token, image)` comes on this thread."""

    ready = Signal(object, QImage)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._outbox = Outbox(self)
        self._wanted = _Wanted()

    def request(self, token, uri, path, mtime, size, content_type):
        QThreadPool.globalInstance().start(
            _Task(token, uri, path, mtime, size, content_type, self._outbox, self._wanted)
        )

    def forget(self):
        """Nothing asked for so far is wanted any more: what has not started is not made,
        and gives no answer. For a folder that was left, whose thumbnails would otherwise
        keep the threads busy long after."""
        self._wanted.round += 1
