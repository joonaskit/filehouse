# Filehouse

A small file browser for KDE Plasma (Wayland), written in Python and Qt Widgets. It is a companion to [Quickshell Island](https://github.com/joonaskit/Quickshell-Dynamic-Island) and follows the shell's theme, but runs as its own process and works without the shell.

It does not try to replace Dolphin. It covers everyday browsing and hands off to the default file manager for the rest.

## Status: alpha

Everyday browsing and file operations work, and operations show in the shell's island when it is running. The design and the plan are in [ARCHITECTURE.md](ARCHITECTURE.md); what is left is on the [issue list](https://github.com/joonaskit/filehouse/issues).

**Browsing**

- Breadcrumb path bar, editable with a click or Ctrl+L; back, forward and up, with history per pane
- Details view (name, permissions, modified, size) and an icons view with thumbnails; sort by name, modified or size; hidden files on request
- Folders load in batches on a worker thread and follow changes made elsewhere
- A sidebar with home, the special folders, **Recent** (the files used lately), the trash and the drives, with a usage bar per drive and eject
- **Tabs** (Ctrl+T, or middle-click a folder) and a **split view** (F3) with two folders side by side; each tab has its own split. Drop files on a tab or on the other pane
- **Favourites**: a group in the sidebar (Ctrl+D adds the open folder, or drop a folder on it). They are Dolphin's places, so the two share them, and they can be put in another order by dragging. The groups of the sidebar fold shut with a click on their heading
- A narrow layout below 800 pixels: the sidebar becomes a rail of icons, the actions a bar under the list, and the details a sheet

**Looking at files**

- A details pane with what is selected: type, dates, size, owner, a grid of the permissions, and a thumbnail
- **Preview** with Space, as Quick Look on the Mac: pictures (and the first page of a PDF), text and code, with zoom, pan and full screen. The arrow keys go on moving the selection, and the preview follows
- Thumbnails come from the shared freedesktop cache (so what Dolphin made is shown), from Qt, and from the desktop's `.thumbnailer` programs (for videos, for example, when one such as `ffmpegthumbnailer` is installed)
- Open with the default app, Open with, Open folder in the default file manager, and Open terminal here
- Files opened from the app go in the desktop's lists of recent files (the freedesktop one and Plasma's), unless **Don't remember opened files** is on in the folder menu or Plasma's Recent Files setting says not to

**Changing files**

- Copy, cut and paste, drag and drop (a move on the same file system, a copy across), Move to…, rename, new folder and new file
- Move to trash, with restore and empty; permanent delete asks first and never happens by itself
- Permission presets (Private, Shared, Make executable, Read-only) and a properties dialog
- **Archives**: Extract here, Extract to… and Compress… (zip or tar.gz), each a background operation. Reads zip, tar, 7z, rar and more through libarchive, asks for the password of a protected zip, and never writes outside the folder it extracts into or over what is there
- Operations run in the background, with progress, cancel, retry and error details. A cancel reports how far the job got, and a move never removes a file before its copy is complete
- Closing the last window while jobs run offers to let them finish in the background; a desktop notification tells how it went when no window is left to show it

**With the shell**

- Operations show in the island with progress, Cancel and Retry
- Colours, fonts, corner radii and scale come from the shell's `theme.json`, with built-in defaults when it is missing. Colours and radii follow changes while the app runs; the scales and fonts apply at the next start
- It works the same with the shell stopped

## Install

Fedora:

```sh
sudo dnf install python3-pyside6 python3-gobject python3-libarchive-c
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

Use the distribution's PySide6 and PyGObject rather than the ones from PyPI, so that Qt and Gio share one event loop. `python3-libarchive-c` is for extracting and compressing archives; without it the app runs and leaves those out of the menu.

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
| Space | Preview (Space again closes it) |
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
| Ctrl+Alt+C | Copy the path |
| Shift+F4 | Open terminal here |
| Ctrl+1, Ctrl+3 | Icons, details |
| Alt+Enter | Properties |
| F3 | Split the view in two |
| Shift+F5, Shift+F6 | Copy, move to the other pane |
| Ctrl+T | New tab |
| Ctrl+Tab, Ctrl+Shift+Tab | Next, previous tab (also Ctrl+PageDown, Ctrl+PageUp) |
| Ctrl+W | Close the tab, or the window with the last one |

In the preview: the arrow keys, Home, End and Page Up/Down move the selection of the list; the wheel, + and − zoom a picture, 0 fits it again; Return or a double click go full screen; Escape leaves full screen, then closes.

## Tests

```sh
pytest
ruff check . && ruff format --check .
```
