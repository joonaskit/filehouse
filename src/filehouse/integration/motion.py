"""How long things take to move, and along which curve.

The shell's values, from `docs/ANIMATION_CATALOGUE.md` in the quickshell repo (its
`Theme.qml`): they are not in `theme.json`, so this is a copy. Nothing in `ui/` has a
duration or an easing of its own.

The desktop's animation speed scales every duration, and at zero nothing moves.
"""

import os
import time
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPropertyAnimation

# A panel opening or closing: the shell's `animDurationPopover`. Its own slides take
# 360 ms (`animDuration`), which is for a pill that morphs; a panel of a file browser
# that is opened often is to be out of the way sooner.
SLIDE_MS = 160
SLIDE_EASING = QEasingCurve.Type.OutCubic
# A progress bar filling: `animDurationProgress`, with the curve the shell's bars have
PROGRESS_MS = 300
PROGRESS_EASING = QEasingCurve.Type.OutQuad

_GROUP = "[KDE]"
_KEY = "AnimationDurationFactor"
# The setting is read again when it is this old: it changes while the app runs
_FRESH_S = 2.0
_read = None  # (factor, when, of which files)


def _config_files():
    home = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    system = os.environ.get("XDG_CONFIG_DIRS") or "/etc/xdg"
    return [Path(folder) / "kdeglobals" for folder in [home, *system.split(":")] if folder]


def _factor_in(path):
    """The factor a kdeglobals file sets, or None."""
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return None
    group = None
    for line in lines:
        line = line.strip()
        if line.startswith("["):
            group = line
        elif group == _GROUP and "=" in line:
            key, _, value = line.partition("=")
            # A key may carry a mark such as "[$i]"
            if key.split("[")[0].strip() == _KEY:
                try:
                    return max(0.0, float(value))
                except ValueError:
                    return None
    return None


def factor():
    """Plasma's animation speed: 1 as it comes, 0 for no animations. 1 anywhere else."""
    global _read
    now = time.monotonic()
    files = _config_files()
    if _read is None or now - _read[1] > _FRESH_S or _read[2] != files:
        found = (_factor_in(path) for path in files)
        _read = (next((value for value in found if value is not None), 1.0), now, files)
    return _read[0]


def ms(duration):
    """A duration at the desktop's speed. Zero means: don't animate."""
    return round(duration * factor())


def animation(target, name, easing, parent):
    """An animation of a property. Its duration is set when it starts: see `ms`."""
    made = QPropertyAnimation(target, name, parent)
    made.setEasingCurve(easing)
    return made
