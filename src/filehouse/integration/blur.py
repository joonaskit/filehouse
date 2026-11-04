"""KWin's blur behind a window: the frosted half of the glass look.

KWindowSystem has the call (`KWindowEffects::enableBlurBehind`) but no Python binding
on Fedora, so it is reached through ctypes, with the window's QWindow and a QRegion
passed as the C++ objects PySide wraps. Without the library, the call or a compositor
that blurs, nothing happens and the glass is drawn more solid (`style`).
"""

import ctypes

import shiboken6
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QRegion

_LIBRARY = "libKF6WindowSystem.so.6"
# KWindowEffects::Effect::BlurBehind
_BLUR_BEHIND = 7

_calls = None
_available = None


def _load():
    """The two calls, or None without the library."""
    global _calls
    if _calls is None:
        try:
            lib = ctypes.CDLL(_LIBRARY)
            available = lib._ZN14KWindowEffects17isEffectAvailableENS_6EffectE
            enable = lib._ZN14KWindowEffects16enableBlurBehindEP7QWindowbRK7QRegion
        except (OSError, AttributeError):
            _calls = False
        else:
            available.argtypes = [ctypes.c_int]
            available.restype = ctypes.c_bool
            enable.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_void_p]
            enable.restype = None
            _calls = (available, enable)
    return _calls or None


def available():
    """Whether windows can be blurred behind. Asked once: the first answer is no until
    the compositor's reply has been read, so the events are let through for it."""
    global _available
    if _available is None:
        calls = _load()
        app = QCoreApplication.instance()
        if calls is None or app is None:
            return False
        _available = calls[0](_BLUR_BEHIND)
        if not _available:
            app.processEvents()
            _available = calls[0](_BLUR_BEHIND)
    return _available


def behind(widget, on, region=None):
    """Blur behind the top-level `widget`, in `region` (its own coordinates; all of it
    without one), or stop. Does nothing before it has a window."""
    window = widget.windowHandle()
    if window is None or not available():
        return
    region = region if region is not None else QRegion()
    _load()[1](shiboken6.getCppPointer(window)[0], on, shiboken6.getCppPointer(region)[0])
