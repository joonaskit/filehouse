"""Opening files with other apps, and handing folders off to the default file manager
and the terminal."""

import os
import shlex
from dataclasses import dataclass, field
from functools import cache

from gi.repository import Gio, GLib

from filehouse import APP_ID

_OWN_DESKTOP_ID = f"{APP_ID}.desktop"

# Tried in this order when the desktop names no terminal
_TERMINALS = (
    "xdg-terminal-exec",
    "konsole",
    "ptyxis",
    "kgx",
    "gnome-terminal",
    "alacritty",
    "kitty",
    "foot",
    "wezterm",
    "xterm",
)


@dataclass(frozen=True)
class App:
    name: str
    icon: str | None  # an icon theme name or a file path
    _info: Gio.AppInfo = field(repr=False, compare=False)


def _app(info):
    icon = info.get_icon()
    if isinstance(icon, Gio.ThemedIcon):
        icon_name = icon.get_names()[0]
    elif isinstance(icon, Gio.FileIcon):
        icon_name = icon.get_file().get_path()
    else:
        icon_name = None
    return App(info.get_display_name(), icon_name, info)


def open_default(location, on_error):
    """Open with the default app. `on_error(message)` is called if that fails."""

    def finished(_source, result):
        try:
            Gio.AppInfo.launch_default_for_uri_finish(result)
        except GLib.Error as error:
            on_error(f"Could not open {location.get_basename()}: {error.message}")

    Gio.AppInfo.launch_default_for_uri_async(location.get_uri(), None, None, finished)


def apps_for_type(content_type):
    """Apps that can open this type, the default one first."""
    return [
        _app(info)
        for info in Gio.AppInfo.get_all_for_type(content_type)
        if info.get_id() != _OWN_DESKTOP_ID
    ]


def open_with(app, location, on_error):
    def finished(info, result):
        try:
            info.launch_uris_finish(result)
        except GLib.Error as error:
            on_error(f"Could not start {app.name}: {error.message}")

    app._info.launch_uris_async([location.get_uri()], None, None, finished)


def file_manager():
    """The default file manager other than this app, or None."""
    apps = apps_for_type("inode/directory")
    return apps[0] if apps else None


def _configured_terminal():
    """The terminal chosen in Plasma's settings, as a command name, or None."""
    settings = GLib.KeyFile()
    try:
        settings.load_from_file(
            os.path.join(GLib.get_user_config_dir(), "kdeglobals"), GLib.KeyFileFlags.NONE
        )
        words = shlex.split(settings.get_string("General", "TerminalApplication"))
    except (GLib.Error, ValueError):
        return None
    return words[0] if words else None


def terminal(find=GLib.find_program_in_path):
    """The program to run as the terminal, as a full path, or None when there is none.
    What the user chose comes first: in Plasma's settings, then in $TERMINAL."""
    candidates = [_configured_terminal(), os.environ.get("TERMINAL"), *_TERMINALS]
    for name in candidates:
        path = find(name) if name else None
        if path:
            return path
    return None


@cache
def default_terminal():
    """`terminal()`, looked up once: it is asked for every time a menu is made."""
    return terminal()


def open_terminal(folder_path, on_error):
    """Start the terminal in a folder. A terminal opens its shell where it is started."""
    program = default_terminal()
    if program is None:
        on_error("No terminal was found.")
        return
    launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.NONE)
    launcher.set_cwd(folder_path)
    try:
        launcher.spawnv([program])
    except GLib.Error as error:
        on_error(f"Could not start the terminal: {error.message}")
