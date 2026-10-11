"""Cut, copy and paste of files, in the formats Dolphin uses."""

from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

# Present, with the value "1", when the files were cut
CUT_MIME = "application/x-kde-cutselection"

# What this app last put on the clipboard
_ours = None


def put(uris, cut):
    data = QMimeData()
    data.setUrls([QUrl(uri) for uri in uris])
    if cut:
        data.setData(CUT_MIME, b"1")
    global _ours
    _ours = data
    QApplication.clipboard().setMimeData(data)


def get():
    """The file URIs on the clipboard and whether they were cut."""
    return read(QApplication.clipboard().mimeData())


def read(data):
    if data is None or not data.hasUrls():
        return [], False
    uris = [url.toString(QUrl.ComponentFormattingOption.FullyEncoded) for url in data.urls()]
    return uris, bytes(data.data(CUT_MIME)) == b"1"


def clear():
    QApplication.clipboard().clear()


def release():
    """Let go of what this app put on the clipboard. Call when the app quits.

    The data would not outlive the process anyway (a clipboard manager has its
    own copy), and on some platforms Qt would otherwise free it after Python
    has shut down, which crashes on exit.
    """
    board = QApplication.clipboard()
    # Only when it is still ours: never empty what another app has copied since
    if _ours is not None and isValid(_ours) and board.mimeData() is _ours:
        board.clear()
