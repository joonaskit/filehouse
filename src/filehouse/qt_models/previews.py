"""Loads a picture for the preview window, on a worker thread.

Not a thumbnail: as large as the screen is worth, and not kept in any cache.
"""

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage, QImageReader

from filehouse.qt_models import thumbnails

# The longest side of what is loaded. A larger picture is scaled while decoding, which
# is much cheaper than decoding it whole
MAX_SIDE = 4096
# A file above this is not previewed
MAX_BYTES = 256 * 1024 * 1024
# A document's page is drawn at its own size, which is small: at least this high
_PDF_HEIGHT = 1400


def can_preview(content_type, size):
    return size <= MAX_BYTES and thumbnails.can_thumbnail(content_type)


def load(path, content_type):
    """The picture, upright. A null image when it can't be read."""
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    full = reader.size()
    if full.isValid():
        target = None
        if max(full.width(), full.height()) > MAX_SIDE:
            target = full.scaled(QSize(MAX_SIDE, MAX_SIDE), Qt.AspectRatioMode.KeepAspectRatio)
        elif content_type == "application/pdf" and full.height() < _PDF_HEIGHT:
            target = full.scaled(
                QSize(_PDF_HEIGHT * 4, _PDF_HEIGHT), Qt.AspectRatioMode.KeepAspectRatio
            )
        if target is not None:
            reader.setScaledSize(target)
    return reader.read()


class _Signals(QObject):
    ready = Signal(object, QImage)


class _Task(QRunnable):
    def __init__(self, token, path, content_type, signals):
        super().__init__()
        self._arguments = (token, path, content_type)
        self._signals = signals

    def run(self):
        token, path, content_type = self._arguments
        image = load(path, content_type)
        try:
            self._signals.ready.emit(token, image)
        except RuntimeError:
            # The asker is gone
            pass


class PreviewLoader(QObject):
    """`ready(token, image)` comes on this thread, with a null image when it failed."""

    ready = Signal(object, QImage)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._signals = _Signals(self)
        self._signals.ready.connect(self.ready)

    def request(self, token, path, content_type):
        QThreadPool.globalInstance().start(_Task(token, path, content_type, self._signals))
