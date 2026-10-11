"""Adapts core/ to Qt: runs core work on threads and turns callbacks into queued signals."""

import gi

gi.require_version("Gio", "2.0")
