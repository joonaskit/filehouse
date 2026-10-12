"""Loads what the preview window shows, on a worker thread: a picture, or the start of
a text file.

Not a thumbnail: as large as the screen is worth, and not kept in any cache.
"""

import codecs

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage, QImageReader

from filehouse.qt_models import thumbnails
from filehouse.qt_models.icons import kind_of

# The longest side of what is loaded. A larger picture is scaled while decoding, which
# is much cheaper than decoding it whole
MAX_SIDE = 4096
# A file above this is not previewed
MAX_BYTES = 256 * 1024 * 1024
# A document's page is drawn at its own size, which is small: at least this high
_PDF_HEIGHT = 1400


# How much of a text file is read: the start is all a preview shows
TEXT_BYTES = 64 * 1024


def can_preview_picture(content_type, size):
    return size <= MAX_BYTES and thumbnails.can_thumbnail(content_type)


def can_preview_text(content_type):
    """Plain text and what is written as text: code, data, markup. Whether the file really
    is, is told when it is read."""
    return content_type.startswith("text/") or kind_of(content_type) == "code"


def can_preview(content_type, size):
    return can_preview_picture(content_type, size) or can_preview_text(content_type)


def read_text(path):
    """The start of a text file as `(text, truncated)`, or None when it is not text:
    a file with NUL bytes is not, and a file that is not UTF-8 is read as Western
    European, which every byte sequence is."""
    try:
        with open(path, "rb") as f:
            data = f.read(TEXT_BYTES + 1)
    except OSError:
        return None
    truncated = len(data) > TEXT_BYTES
    data = data[:TEXT_BYTES]
    if b"\0" in data and not data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return None
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        # Not final: the cut may fall inside a character
        decoder = codecs.getincrementaldecoder("utf-16")(errors="replace")
        return decoder.decode(data, final=not truncated), truncated
    decoder = codecs.getincrementaldecoder("utf-8-sig")()
    try:
        # Not final: a cut inside a character is not an error
        return decoder.decode(data, final=not truncated), truncated
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace"), truncated


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
    text_ready = Signal(object, object, bool)


class _Task(QRunnable):
    def __init__(self, token, path, content_type, size, signals):
        super().__init__()
        self._arguments = (token, path, content_type, size)
        self._signals = signals

    def run(self):
        token, path, content_type, size = self._arguments
        try:
            if can_preview_picture(content_type, size):
                self._signals.ready.emit(token, load(path, content_type))
            else:
                text = read_text(path)
                self._signals.text_ready.emit(
                    token, None if text is None else text[0], text is not None and text[1]
                )
        except RuntimeError:
            # The asker is gone
            pass


class PreviewLoader(QObject):
    """`ready(token, image)` comes on this thread, with a null image when it failed.
    For a text file it is `text_ready(token, text, truncated)`, with None for a file
    that turned out not to be text."""

    ready = Signal(object, QImage)
    text_ready = Signal(object, object, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._signals = _Signals(self)
        self._signals.ready.connect(self.ready)
        self._signals.text_ready.connect(self.text_ready)

    def request(self, token, path, content_type, size):
        QThreadPool.globalInstance().start(_Task(token, path, content_type, size, self._signals))
