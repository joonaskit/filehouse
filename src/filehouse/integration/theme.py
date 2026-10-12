"""Reader for the theme exported by Quickshell Island.

A port of the shell's examples/read_theme.py (format: docs/THEME.md in the
shell repo): start from complete defaults, overlay whatever the file
provides, and keep working when the file is missing, unreadable or from a
newer format version.
"""

import copy
import json
import os
import re

SUPPORTED_VERSION = 1

# Used as-is when the shell has never run, and for any field the file lacks
DEFAULTS = {
    "version": SUPPORTED_VERSION,
    "scheme": "dark",
    "colors": {
        "background": "#000000",
        "surface": "#1c1c1e",
        "surfaceRaised": "#2c2c2e",
        "textPrimary": "#ffffff",
        "textSecondary": "#98989d",
        "textTertiary": "#636366",
        "onAccent": "#ffffff",
        "accent": "#0a84ff",
        "success": "#30d158",
        "warning": "#ff9f0a",
        "danger": "#ff453a",
        # For categories and decoration: the icon tiles
        "palette": {
            "green": "#30d158",
            "blue": "#0a84ff",
            "orange": "#ff9f0a",
            "yellow": "#ffd60a",
            "red": "#ff453a",
            "purple": "#bf5af2",
            "cyan": "#64d2ff",
            "indigo": "#5e5ce6",
        },
    },
    "fonts": {
        "families": ["sans-serif"],
        "displayFamilies": ["sans-serif"],
    },
    "radius": {"small": 6, "medium": 10, "large": 14},
    "scale": {"ui": 1.0, "font": 1.0},
}

_COLOR = re.compile(r"#[0-9a-fA-F]{6}")


def theme_path():
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(config_home, "quickshell-island", "theme.json")


def _usable(section, default, value):
    """Whether `value` can stand in for `default`. A wrong type falls back to the default."""
    if section in ("colors", "palette"):
        return isinstance(value, str) and _COLOR.fullmatch(value) is not None
    if isinstance(default, bool) or isinstance(value, bool):
        return isinstance(default, bool) and isinstance(value, bool)
    if isinstance(default, int | float):
        return isinstance(value, int | float) and value > 0
    if isinstance(default, list):
        return bool(value) and isinstance(value, list) and all(isinstance(v, str) for v in value)
    return isinstance(value, type(default))


def merge(defaults, loaded, section=None):
    """Overlay `loaded` on `defaults`, section by section.

    Unknown fields are kept, so a newer file with extra fields still loads.
    A known field with the wrong type or shape is ignored and the default is used.
    """
    result = copy.deepcopy(defaults)
    for key, value in loaded.items():
        if key not in defaults:
            result[key] = value
            continue
        default = defaults[key]
        if isinstance(default, dict):
            if isinstance(value, dict):
                result[key] = merge(default, value, key)
        elif _usable(section, default, value):
            result[key] = value
    return result


def load_theme(path=None):
    """Return the theme as a dict. Never raises; falls back to DEFAULTS."""
    try:
        with open(path or theme_path(), encoding="utf-8") as f:
            loaded = json.load(f)
    except (OSError, ValueError):
        return merge(DEFAULTS, {})
    if not isinstance(loaded, dict):
        return merge(DEFAULTS, {})
    version = loaded.get("version")
    # A higher version means fields were removed or changed meaning
    if not isinstance(version, int) or isinstance(version, bool) or version > SUPPORTED_VERSION:
        return merge(DEFAULTS, {})
    return merge(DEFAULTS, loaded)


def scaled(theme, px):
    """A size in pixels at the user's UI scale, as the shell's Theme.px does."""
    return round(px * theme["scale"]["ui"])


def font_scaled(theme, px):
    """A font size in pixels, as the shell's Theme.fontPx does."""
    return max(8, round(px * theme["scale"]["font"] * theme["scale"]["ui"]))
