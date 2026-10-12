"""Thumbnails of images, shared with other file managers through the freedesktop cache.

A thumbnail is looked up in `$XDG_CACHE_HOME/thumbnails/normal/<md5 of the URI>.png`
and used when its `Thumb::MTime` matches the file. Otherwise it is made here and
written back the way the thumbnail spec asks, so Dolphin and others can use it too.
"""

import hashlib
import os
import tempfile

from PySide6.QtCore import QObject, QRunnable, QSize, QStandardPaths, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage, QImageReader

# The spec's "normal" size
SIZE = 128
# Decoding a huge image for a small picture is not worth the wait
_MAX_BYTES = 64 * 1024 * 1024

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


class _Signals(QObject):
    # A token from the asker, and the image: null when there is no thumbnail to show
    ready = Signal(object, QImage)


class _Task(QRunnable):
    def __init__(self, token, uri, path, mtime, size, signals):
        super().__init__()
        self._arguments = (uri, path, mtime, size)
        self._token = token
        self._signals = signals

    def run(self):
        uri, path, mtime, size = self._arguments
        image = load(uri, mtime) or make(uri, path, mtime, size) or QImage()
        try:
            self._signals.ready.emit(self._token, image)
        except RuntimeError:
            # The asker is gone
            pass


class Thumbnailer(QObject):
    """Loads or makes thumbnails on worker threads. `ready(token, image)` comes on this thread."""

    ready = Signal(object, QImage)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._signals = _Signals(self)
        self._signals.ready.connect(self.ready)

    def request(self, token, uri, path, mtime, size):
        QThreadPool.globalInstance().start(_Task(token, uri, path, mtime, size, self._signals))
