# Filehouse

A small file browser for KDE Plasma (Wayland), written in Python and Qt Widgets. It is a companion to [Quickshell Island](https://github.com/joonaskit/Quickshell-Dynamic-Island) and follows the shell's theme, but runs as its own process and works without the shell.

It does not try to replace Dolphin. It covers everyday browsing and hands off to the default file manager for the rest.

## Status: alpha

Everyday browsing and file operations work, and operations show in the shell's island when it is running. The design and the plan are in [ARCHITECTURE.md](ARCHITECTURE.md); what is left is on the [issue list](https://github.com/joonaskit/filehouse/issues).

**Browsing**

- Breadcrumb path bar, editable with a click or Ctrl+L; back, forward and up, with history per pane
- Details view (name, permissions, modified, size) and an icons view with thumbnails; sort by name, modified or size; hidden files on request
- **Each folder keeps its view and sort order**: Downloads the newest first, Pictures as icons. "Use the default here" in the View menu gives a folder's own up, and the settings can make it one view for every folder
- Folders load in batches on a worker thread and follow changes made elsewhere
- A sidebar with home, the special folders, **Recent** (the files used lately), the trash and the drives, with a usage bar per drive and eject
- **Tabs** (Ctrl+T, or middle-click a folder) and a **split view** (F3) with two folders side by side; each tab has its own split. Drop files on a tab or on the other pane. A start without a path opens the tabs and splits the last window had
- **Favourites**: a group in the sidebar (Ctrl+D adds the open folder, or drag folders to the group: to its heading, or to the edge of a favourite to put them there). They are Dolphin's places, so the two share them, and they can be put in another order by dragging. The groups of the sidebar fold shut with a click on their heading
- The details pane can be put away with F11, the View menu, a toolbar button or the settings; it stays away in every window until it is asked back
- **Search** (Ctrl+F): typing filters the open folder at once and finds names in the folders below it, with the folder of each result in a column
- **Selecting**: by a pattern or a few words (Ctrl+S: `*.jpg`, or "holiday 2024"), inverted (Ctrl+Shift+A), and all of one type from the menu of a file
- A narrow layout below 800 pixels: the sidebar becomes a rail of icons, the search field opens from a button, and the details are a sheet opened from the button in the corner under the rail

**Looking at files**

- A details pane with what is selected: type, dates, size, owner, a grid of the permissions, and a thumbnail
- **Preview** with Space, as Quick Look on the Mac: pictures, PDFs a page at a time (Page Up and Page Down), text and code, with zoom, pan and full screen. The arrow keys go on moving the selection, and the preview follows. In a narrow window it is a sheet at the bottom
- Thumbnails come from the shared freedesktop cache (so what Dolphin made is shown), from Qt, and from the desktop's `.thumbnailer` programs (for videos, for example, when one such as `ffmpegthumbnailer` is installed)
- Open with the default app, Open with, Open folder in the default file manager, and Open terminal here
- Files opened from the app go in the desktop's lists of recent files (the freedesktop one and Plasma's), unless **Don't remember opened files** is on in the folder menu or Plasma's Recent Files setting says not to

**Changing files**

- Copy, cut and paste, drag and drop (a move on the same file system, a copy across), Move to…, rename, new folder and new file
- **Duplicate** (a copy beside the file, "report (2).pdf") and **Create link** (a symbolic link beside it, or in another folder by dropping with Ctrl+Shift or from the Alt drop menu)
- **Rename several at once** (F2 with several selected): find and replace, a name with a number, or upper and lower case, with every new name shown first. A name that is taken blocks all of it, so nothing is half renamed, and nothing is ever replaced
- Move to trash, with restore and empty; permanent delete asks first and never happens by itself
- Permission presets (Private, Shared, Make executable, Read-only) and a properties dialog
- **Archives**: Extract here, Extract to… and Compress… (zip or tar.gz), each a background operation. Reads zip, tar, 7z, rar and more through libarchive, asks for the password of a protected zip, and never writes outside the folder it extracts into or over what is there
- Operations run in the background, with progress, cancel, retry and error details. A cancel reports how far the job got, and a move never removes a file before its copy is complete
- Closing the last window while jobs run offers to let them finish in the background; a desktop notification tells how it went when no window is left to show it
- With its last window closed the app keeps running in the background, so the next window opens in about a third of the time. A setting turns that off, another starts it with your login, and `filehouse --quit` ends it. While it runs it has an icon in the system tray (the shell's row of running apps): a click opens a window, and its menu has Quit

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

There is one process per session: a second `filehouse` hands its folder to the one that runs, which opens a window for it. Other apps' "Show in folder" comes to it too (`org.freedesktop.FileManager1`). To open folders with it by default:

```sh
gio mime inode/directory filehouse.desktop
```

`install.sh` also installs the service file that lets the session bus start the app for "Show in folder", and tells the bus to read it. Dolphin answers those calls for as long as it runs, its background process included (`systemctl --user stop plasma-dolphin.service`).

To show the app in the launcher and the shell's dock, install the desktop file and make sure `filehouse` is on your `PATH`:

```sh
install -Dm644 data/filehouse.desktop ~/.local/share/applications/filehouse.desktop
```

## Keyboard

| Key | Action |
| --- | --- |
| Enter | Open |
| Space | Preview (Space again closes it) |
| Backspace, Alt+Left, the mouse's back button | Back |
| Alt+Right, the mouse's forward button | Forward |
| Alt+Up | Up |
| Ctrl+L | Edit the location |
| Ctrl+H | Show hidden files |
| F5 | Reload |
| Ctrl+C, Ctrl+X, Ctrl+V | Copy, cut, paste |
| Delete | Move to trash |
| Shift+Delete | Delete permanently (asks first) |
| F2 | Rename (several at once when several are selected) |
| Ctrl+S | Select by a pattern or words |
| Ctrl+Shift+A | Invert the selection |
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
