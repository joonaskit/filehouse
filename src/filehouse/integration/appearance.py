"""The look chosen by hand, over the shell's theme: the scheme, the accent and the
corners, with the shell's own values for each (quickshell's `services/Theme.qml`), so
the app chosen by hand looks as the shell would with the same choices (#30). The same
as Lumina's.

`theme.py` reads the shell's file. A `Look` is laid over what it read; the fonts and
the scales stay the shell's, or the built-in defaults without it.
"""

import copy
import os
from dataclasses import dataclass

from PySide6.QtGui import QColor

from filehouse.integration import style, theme

SCHEMES = (("dark", "Dark"), ("light", "Light"))
# The shell's accents, in its order; the colours are the theme's palette, by name
ACCENTS = (
    ("blue", "Blue"),
    ("indigo", "Indigo"),
    ("purple", "Purple"),
    ("red", "Red"),
    ("orange", "Orange"),
    ("yellow", "Yellow"),
    ("green", "Green"),
    ("cyan", "Cyan"),
)
# The shell's corner roundness: its radii (6, 10 and 14) times the scale
CORNERS = (
    ("square", "Square", 0.2),
    ("tight", "Tight", 0.6),
    ("default", "Default", 1.0),
    ("round", "Round", 1.5),
)
_RADII = {"small": 6, "medium": 10, "large": 14}

# The shell's colours for each scheme, as it exports them: the island's background,
# the cards and their hover, and the text
_SCHEME_COLORS = {
    "dark": {
        "background": "#000000",
        "surface": "#1c1c1e",
        "surfaceRaised": "#2c2c2e",
        "textPrimary": "#ffffff",
        "textSecondary": "#98989d",
        "textTertiary": "#636366",
    },
    "light": {
        "background": "#faf6ef",
        "surface": "#f1ebe1",
        "surfaceRaised": "#e7dfd2",
        "textPrimary": "#221f1a",
        "textSecondary": "#575147",
        "textTertiary": "#787166",
    },
}


@dataclass(frozen=True)
class Look:
    """A scheme, an accent and a corner roundness, by name."""

    scheme: str = "dark"
    accent: str = "blue"
    corners: str = "default"

    def __post_init__(self):
        # A name not known (from an older or a hand-edited settings file) is the default's
        defaults = Look.__dataclass_fields__
        for field, known in (
            ("scheme", [name for name, _label in SCHEMES]),
            ("accent", [name for name, _label in ACCENTS]),
            ("corners", [name for name, _label, _scale in CORNERS]),
        ):
            if getattr(self, field) not in known:
                object.__setattr__(self, field, defaults[field].default)


def shell_found(path=None):
    """Whether the shell has written its theme: then the look follows it unless the user
    chose by hand."""
    return os.path.exists(path or theme.theme_path())


def look_of(made):
    """The Look a theme has: its scheme, the accent of the palette it has as accent (blue
    for any other), and the corner roundness nearest its medium radius."""
    accent = made["colors"]["accent"].lower()
    palette = made["colors"]["palette"]
    accents = [name for name, _label in ACCENTS if palette.get(name, "").lower() == accent]
    medium = made["radius"]["medium"] / _RADII["medium"]
    corners = min(CORNERS, key=lambda corner: abs(corner[2] - medium))[0]
    return Look(made.get("scheme", "dark"), accents[0] if accents else "blue", corners)


def theme_with(base, look):
    """`base`, a theme as `theme.load_theme` gives it, with `look` over it: or `base` as
    it is for no look (following the shell)."""
    if look is None:
        return base
    made = copy.deepcopy(base)
    made["scheme"] = look.scheme
    made["colors"].update(_SCHEME_COLORS[look.scheme])
    accent = made["colors"]["palette"][look.accent]
    made["colors"]["accent"] = accent
    made["colors"]["onAccent"] = _on(accent)
    scale = next(scale for name, _label, scale in CORNERS if name == look.corners)
    # As the shell rounds them (Math.round): half up
    made["radius"] = {name: int(size * scale + 0.5) for name, size in _RADII.items()}
    return made


def apply(app, look, live=True):
    """Style `app` with the shell's theme as it is now and `look` over it (None: the
    shell's). Live, as the shell's changes are: the colours and the corners change at
    once, the fonts and the scales stay."""
    return style.apply(app, theme_with(theme.load_theme(), look), live=live)


def _on(accent):
    """Text on the accent, as the shell has it: black on the light accents (yellow, cyan,
    green), white on the others."""
    colour = QColor(accent)
    light = 0.2126 * colour.redF() + 0.7152 * colour.greenF() + 0.0722 * colour.blueF()
    return "#000000" if light > 0.6 else "#ffffff"
