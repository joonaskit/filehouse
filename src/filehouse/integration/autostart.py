"""Starting with the session, so the first window after a login opens as soon as the rest.

An entry in the desktop's autostart folder starts the app as the session bus does for
another app's "Show in folder": without a window, staying in the background. Whether it
is on is whether the file is there, so the desktop's own list of what starts at a login
(System Settings > Autostart) turns it off as well. No Qt and no Gio: a file is all.
"""

import logging
import os
import shutil
import sys

from filehouse import APP_ID

log = logging.getLogger(__name__)

# Not the name of the app's own .desktop file: this one starts it without a window
_FILE = f"{APP_ID}-background.desktop"
_ENTRY = """[Desktop Entry]
Type=Application
Name=Filehouse (in the background)
Comment=Keeps Filehouse running, so its windows open sooner
Exec={command} --service
Icon=filehouse
NoDisplay=true
"""


def path():
    config = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(config, "autostart", _FILE)


def enabled():
    return os.path.isfile(path())


def command():
    """The command this process was started with, as a login can run it: its whole
    path, since a session's PATH need not have the folder it is in."""
    started = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    if os.path.basename(started) == APP_ID and os.access(started, os.X_OK):
        return started
    # Run as a module, or from a test
    return shutil.which(APP_ID) or APP_ID


def _quoted(argument):
    """An argument of an Exec line: in quotes, with what is special in them escaped."""
    for character in '\\"`$':
        argument = argument.replace(character, "\\" + character)
    return f'"{argument}"'


def set_enabled(on):
    """Write the entry or take it away. Whether it is now as asked."""
    target = path()
    try:
        if on:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                f.write(_ENTRY.format(command=_quoted(command())))
        elif os.path.exists(target):
            os.remove(target)
    except OSError as error:
        log.warning("could not change %s: %s", target, error)
    return enabled() == bool(on)
