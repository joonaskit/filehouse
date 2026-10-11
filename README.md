# Filehouse

A small file browser for KDE Plasma (Wayland), written in Python and Qt Widgets. It is a companion to [Quickshell Island](https://github.com/joonaskit/Quickshell-Dynamic-Island) and follows the shell's theme, but runs as its own process and works without the shell.

It does not try to replace Dolphin. It covers everyday browsing and hands off to the default file manager for the rest.

## Status: 0.1 alpha

Browsing and file operations work. Progress in the shell's island is not implemented yet.

- Copy, cut and paste, drag and drop, move to trash, permanent delete, rename and new folder
- Operations run in the background, with progress, cancel, retry and error details in a panel at the bottom of the window
- Details view with sorting: folders first, then natural order by name
- Folders load in batches on a worker thread and follow changes made elsewhere
- Breadcrumb path bar, editable with a click or Ctrl+L
- Back, forward and up, with history per window
- Sidebar with home and the user's special folders
- Open with the default app, Open with, and Open folder in the default file manager
- Colours, fonts, radii and scale from the shell's `theme.json`, with built-in defaults when it is missing

See [ARCHITECTURE.md](ARCHITECTURE.md) for the design and the plan.

## Install

Fedora:

```sh
sudo dnf install python3-pyside6 python3-gobject
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

Use the distribution's PySide6 and PyGObject rather than the ones from PyPI, so that Qt and Gio share one event loop.

## Run

```sh
filehouse                 # opens home
filehouse ~/Downloads     # opens a folder
filehouse ~/notes.txt     # opens the file's folder with the file selected
```

To show the app in the launcher and the shell's dock, install the desktop file and make sure `filehouse` is on your `PATH`:

```sh
install -Dm644 data/filehouse.desktop ~/.local/share/applications/filehouse.desktop
```

## Keyboard

| Key | Action |
| --- | --- |
| Enter | Open |
| Backspace, Alt+Left | Back |
| Alt+Right | Forward |
| Alt+Up | Up |
| Ctrl+L | Edit the location |
| Ctrl+H | Show hidden files |
| F5 | Reload |
| Ctrl+C, Ctrl+X, Ctrl+V | Copy, cut, paste |
| Delete | Move to trash |
| Shift+Delete | Delete permanently (asks first) |
| F2 | Rename |
| Ctrl+Shift+N | New folder |
| Ctrl+W | Close the window |

## Tests

```sh
pytest
ruff check . && ruff format --check .
```
