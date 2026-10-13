"""Making a window of this app the child of a window of another app: a file dialog
belongs to the window that asked for it, stays above it and can be modal to it.

The other window is named by the portal: "x11:<id in hex>" or "wayland:<handle>". On X11
Qt can do it. On Wayland the handle is one of the xdg-foreign protocol, which Qt does
not speak and KDE's KWindowSystem does; that library has no Python binding, so its one
function is called through `ctypes`. Without the library, on another platform or with a
handle that means nothing, the window is simply a window of its own.
"""

import ctypes
import ctypes.util
import logging
from collections import deque

from PySide6.QtCore import QLibraryInfo
from PySide6.QtGui import QGuiApplication, QWindow
from shiboken6 import getCppPointer

log = logging.getLogger(__name__)

# KWindowSystem::setMainWindow(QWindow *, const QString &)
_SET_MAIN_WINDOW = "_ZN13KWindowSystem13setMainWindowEP7QWindowRK7QString"
_LIBRARY = "libKF6WindowSystem.so.6"

# The texts given to KWindowSystem are not copied by it, and it may read one after the
# call (it waits for the window to be shown): the last of them are kept
_kept = deque(maxlen=64)
_function = None


class _QString(ctypes.Structure):
    """A QString of Qt 6 that points at text it does not own, as a string literal does."""

    _fields_ = [("d", ctypes.c_void_p), ("ptr", ctypes.c_void_p), ("size", ctypes.c_ssize_t)]


def attach(window, handle):
    """Make `window` (a QWindow) a child of the window `handle` names. Whether it was
    done, as far as can be told."""
    kind, _, identifier = handle.partition(":")
    if not identifier:
        return False
    platform = QGuiApplication.platformName()
    try:
        if kind == "x11" and platform == "xcb":
            return _attach_x11(window, identifier)
        if kind == "wayland" and platform.startswith("wayland"):
            return _attach_wayland(window, identifier)
    except Exception:
        log.exception("could not put a dialog over the window that asked for it")
    return False


def _attach_x11(window, identifier):
    parent = QWindow.fromWinId(int(identifier, 16))
    if parent is None:
        return False
    # Kept by the window it is the parent of
    parent.setParent(window)
    window.setTransientParent(parent)
    return True


def _attach_wayland(window, handle):
    function = _set_main_window()
    if function is None:
        return False
    # A QString's characters are two bytes each, with a zero after the last
    encoded = handle.encode("utf-16-le")
    text = ctypes.create_string_buffer(encoded + b"\0\0")
    string = _QString(None, ctypes.addressof(text), len(encoded) // 2)
    _kept.append((text, string))
    function(getCppPointer(window)[0], ctypes.byref(string))
    return True


def _set_main_window():
    global _function
    if _function is None:
        _function = False
        # The layout of a QString above is that of Qt 6
        if QLibraryInfo.version().majorVersion() == 6:
            try:
                function = getattr(ctypes.CDLL(_LIBRARY), _SET_MAIN_WINDOW)
            except (OSError, AttributeError) as error:
                log.debug("no KWindowSystem, so dialogs are windows of their own: %s", error)
            else:
                function.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
                function.restype = None
                _function = function
    return _function or None
