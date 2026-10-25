# File browser: architecture and implementation plan

A small file browser for KDE Plasma (Wayland), written in Python and Qt. It is a companion app to [Quickshell Island](https://github.com/joonaskit/Quickshell-Dynamic-Island) but runs as its own process and works without the shell.

It is not meant to replace Dolphin on features, stability or security. It covers the behaviour that matters to its author, integrates with the shell, and hands off to Dolphin (or the default file manager) for everything else.

The app is called **filehouse** (app id `filehouse`). The `.desktop` file name, the Wayland app id and `StartupWMClass` are all that string and must change together if it is ever renamed.

## Background

The requirements come from these shell issues:

- **#24**, the integration points between the browser and the shell, each marked must-have, free or later.
- **#21**, the activities API, which shows operation progress in the island. Spec: `docs/ACTIVITIES_API.md` in the shell repo.
- **#22**, the shared theme file. Spec: `docs/THEME.md` in the shell repo.
- **#21 (comment)**, window decorations: start with standard server-side decorations, and revisit a custom titlebar later.
- **#32**, dropping files onto dock apps and pinning folders. Not needed for this app's first version, but the browser's drag-and-drop should produce standard `text/uri-list` so it works once the dock supports it.

### What the first version must do

| # | Integration point (#24) | Status | What the browser does |
| --- | --- | --- | --- |
| 1 | Progress and cancel in the island | **Must-have** | Publish each file operation as an activity; act on Cancel; always end with `done`, `cancelled` or `failed` |
| 2 | Theme | **Must-have, minimal** | Read `theme.json` at startup, with complete built-in defaults when it is missing |
| 6 | Open at a path | **Must-have, trivial** | Accept a path argument; start from a `.desktop` file. No D-Bus name of its own |
| 3a | Dock running state and window list | Free | Ship a `.desktop` file whose `StartupWMClass` exactly equals the Wayland app id |
| 5 | Window switcher | Free | Put the current folder in the window title |
| 3b, 4, 7 | Dock drops, recents, notifications | Later | Not in v1 |

### Safety rules carried from #24

These shape the operations engine more than anything else:

1. Cancel is reliable and reports the real final state, for example "Cancelled after 5 of 12 files".
2. A failure says which file and what error.
3. The app never reports an operation as finished unless every item finished. A progress of `1.0` is not "done"; only an explicit `done` state is.
4. A move never deletes a source before its copy at the destination is complete.
5. Delete goes to the trash by default. Permanent delete always asks first.

## Technology choices

| Concern | Choice | Why |
| --- | --- | --- |
| Language | Python 3.12+ | Matches the shell's helper scripts |
| UI toolkit | **PySide6, Qt Widgets** | Mature item views (`QListView`, `QTreeView`) with selection, rubber band, keyboard navigation, drag and drop and good performance on large folders. Qt Quick would need most of that rebuilt by hand |
| File system | **Gio (PyGObject)** | Copy and move with progress callbacks and a `Gio.Cancellable`, trash, MIME types, default apps and file monitors in one library, all of which the "use existing system libraries" rule in #24 asks for. GVFS locations (`trash://`, `sftp://`, `smb://`, MTP) come for free later |
| D-Bus | **Gio.DBusConnection** | Exact control over variant types (`a{sv}`, `a(ss)`), which the activities API needs and QtDBus makes awkward from Python. It is already a dependency |
| Thumbnails | Freedesktop thumbnail cache, `QImageReader` for what Qt reads (images, and PDFs when Qt has its PDF plugin), the desktop's thumbnailers for the rest | Shares the cache with Dolphin, both ways |
| Archives | **libarchive**, through `libarchive-c` (optional) | Reads nearly every format (zip, tar and its compressions, 7z, rar, cpio) and writes zip and tar in our own job, so progress, cancel and the path checks are ours. Python's `zipfile` and `tarfile` would need no dependency but read no 7z or rar; handing off to Ark would give up the job model |
| Tests | pytest (core), pytest-qt (a few UI tests) | The core has no UI and can be tested against temporary folders |

On Fedora: `dnf install python3-pyside6 python3-gobject`, and `python3-libarchive-c` for archives (without it the app runs and offers nothing for them). For development, a venv created with `--system-site-packages` sees both.

**Decided: Qt Widgets.** Qt Quick would make it easier to match the shell's look and animations, at the cost of building the views by hand.

**Gio inside the Qt event loop.** Qt on Linux runs on the GLib event dispatcher, so Gio callbacks and D-Bus signals attached to the default main context are delivered by Qt's own loop. The phase 0 spike confirmed this with Fedora's PySide6 6.11 on both the Wayland and offscreen platforms: an async Gio call, a GLib idle source and a Gio D-Bus signal subscription all fire while only `app.exec()` runs. The directory model's tests depend on it, so they fail if it ever stops holding. The fallback would be iterating the GLib main context from a `QTimer`.

## Architecture

```
            ┌──────────────────────────── ui/ (Qt Widgets) ───────────────────────────┐
            │ MainWindow · Sidebar · PathBar · FolderView · OperationsPanel · Dialogs │
            └───────▲───────────────────────▲──────────────────────────▲──────────────┘
                    │ Qt model/signals       │ Qt signals                │ palette, fonts
            ┌───────┴────────┐     ┌─────────┴─────────┐       ┌─────────┴─────────┐
            │ DirectoryModel │     │     JobModel      │       │   integration/    │
            │ (qt_models/)   │     │   (qt_models/)    │──────▶│   island.py       │──▶ D-Bus: org.quickshell.IslandActivities
            └───────▲────────┘     └─────────▲─────────┘       │   theme.py        │◀── ~/.config/quickshell-island/theme.json
                    │ callbacks              │ callbacks         │   desktop.py      │──▶ default apps, Dolphin hand-off
            ┌───────┴────────────────────────┴──────────┐       └───────────────────┘
            │                core/ (no Qt)              │
            │ listing · operations (plan, run, cancel)  │
            │ conflicts · errors · locations            │
            └───────────────────▲───────────────────────┘
                                │
                               Gio
```

Rules that keep it this way:

- **`core/` imports no Qt.** It uses Gio and plain Python, and reports through callbacks. This is what makes the safety rules testable with pytest.
- **`qt_models/` adapts the core to Qt.** It runs core work on worker threads and turns callbacks into queued Qt signals, so the core never touches the UI thread.
- **`integration/` holds everything that talks to the shell or the desktop.** Each part works when its counterpart is missing: no island means no activities, no theme file means defaults.
- **The UI never talks to Gio or D-Bus directly.**
- **Nothing on the UI thread waits for the disk** (#61). What a window only looks at (a path typed or named by a start or another app, what is dragged over the sidebar or dropped among the favourites, which special folders are there, what a name typed to save as would replace) is asked on a worker with `workers.Background`, and the answer is used when it is in. A folder that does not answer, on a share that has gone, holds up nothing else. Where it is still asked, only the last question's answer is used; a dialog waits until its answer is in before it answers the app.
- **One source of truth for job state:** `JobModel`. Both the in-app operations panel and the island reporter observe it. The island is a second view of it, never the only one, because the app must work without the shell.

### Project layout

```
filehouse/
├── ARCHITECTURE.md              this document
├── README.md
├── pyproject.toml               entry point: filehouse = filehouse.app:main
├── data/
│   ├── filehouse.desktop     Exec=filehouse %U, StartupWMClass=filehouse
│   ├── filehouse.portal      the backend the file chooser portal can be told to ask
│   └── icons/
├── src/filehouse/
│   ├── app.py                   argument parsing, and whether this process is to be the application
│   ├── application.py           QApplication, desktop file name, the windows, what other processes ask
│   ├── core/
│   │   ├── locations.py         path/URI handling, places (home, Downloads, …), display names
│   │   ├── listing.py           batched directory enumeration, file info records
│   │   ├── search.py            finding files by name in the folders below one
│   │   ├── find.py              `filehouse --search`: that search as JSON lines for another program
│   │   ├── filters.py           the file types a file dialog is asked to offer: globs and MIME types
│   │   ├── history.py           back and forward history for one window
│   │   ├── properties.py        details of a file, size of a folder all the way down
│   │   ├── errors.py            OperationError: which file, which step, readable message
│   │   ├── archives.py          libarchive: reading and writing archives, which paths are safe
│   │   └── operations/
│   │       ├── job.py           Job, JobState, JobProgress
│   │       ├── plan.py          expand a request into concrete steps, count bytes
│   │       ├── copy.py          copy and move (same-device rename, cross-device copy+delete)
│   │       ├── trash.py         trash, and finding there what was trashed
│   │       ├── delete.py        permanent delete
│   │       ├── archive.py       extract and compress, each a job
│   │       ├── conflicts.py     the questions a job asks, answers, names for "Keep both"
│   │       ├── quick.py         new folder, new file, rename and links: immediate, no job
│   │       ├── rename_many.py   several renames as one: the names, the check, all or none
│   │       ├── permissions.py   the permission presets, applied as a job (#63)
│   │       ├── undo.py          what an action did, kept so that it can be taken back
│   │       └── queue.py         runs jobs with a concurrency limit
│   ├── qt_models/
│   │   ├── directory_model.py   QAbstractItemModel over listing + Gio.FileMonitor
│   │   ├── icons.py             icon tile per kind of file
│   │   ├── dates.py             "Today, 10:42", "Yesterday", a weekday, a date
│   │   ├── favourites.py        the favourite folders: one list for the process, followed when the file changes
│   │   ├── previews.py          loads a picture for the preview window on a worker thread
│   │   ├── thumbnails.py        freedesktop thumbnail cache: load, make, write back
│   │   ├── volumes.py           drives and mounts from Gio.VolumeMonitor, mount and eject
│   │   ├── measure.py           folder sizes on a worker thread
│   │   ├── details.py           one file's details on a worker thread
│   │   ├── job_model.py         all jobs of the process, fed by worker threads; titles and texts
│   │   └── workers.py           the thread pool jobs run on, and `Background`: what a window does at once
│   ├── integration/
│   │   ├── theme.py             theme.json reader (port of the shell's examples/read_theme.py)
│   │   ├── style.py             theme → QPalette, fonts and a small stylesheet
│   │   ├── island.py            activities client: JobModel → activities, actions → jobs
│   │   ├── instance.py          one process per session, and org.freedesktop.FileManager1
│   │   ├── portal.py            the file chooser portal's backend: other apps' Open and Save
│   │   ├── parent_window.py     a dialog as the child of another app's window
│   │   ├── theme_watcher.py     follows the theme file while the app runs
│   │   ├── motion.py            how long things take to move, at the desktop's animation speed
│   │   ├── autostart.py         the entry that starts the app with the session
│   │   ├── places.py            the places file Dolphin keeps (user-places.xbel), edited in place
│   │   ├── recent.py            tells the desktop's lists of recent files what was opened
│   │   ├── notifications.py     a desktop notification for a job that ends with no window visible
│   │   └── desktop.py           open, open with, file manager hand-off, the terminal
│   └── ui/
│       ├── main_window.py       layout, tabs and panes, navigation, preview, the narrow window
│       ├── actions.py           the window's actions: text, icon, keys, when each can be used
│       ├── context_menus.py     the menus of an entry or folder, a place, a favourite (#64)
│       ├── file_actions.py      what a window does to files: new file and folder, rename,
│       │                        links, drops, paste, trash and delete, move to, archives,
│       │                        permissions and undo (#64); their file work on a worker, in
│       │                        turn (`workers.Background`, #61)
│       ├── pane.py              one folder: model, sort proxy, list, history, selection
│       ├── chooser.py           the dialog other apps open and save with
│       ├── groups.py            the rounded groups of a toolbar
│       ├── preview.py           the window Space opens: a larger look at the selection
│       ├── media.py             plays video and audio in the preview (QtMultimedia, optional)
│       ├── tab.py               one or two panes and the active one
│       ├── tab_bar.py           the row of tabs, a drop target
│       ├── tray.py              the icon in the tray of a process that stays
│       ├── sidebar.py           places and mounted volumes
│       ├── path_bar.py          breadcrumbs, editable on click or Ctrl+L
│       ├── search_field.py      the search field of the toolbar
│       ├── folder_view.py       list and grid modes, delegates
│       ├── operations_panel.py  in-app list of jobs, with cancel, retry and details
│       ├── job_status.py        the running jobs as one line in the status bar
│       ├── sheet.py             a panel that slides up from the bottom, for the narrow window
│       ├── slide.py             showing and hiding a part of the window by sliding it
│       ├── progress_bar.py      the bar of a job, filling from one report to the next
│       ├── details_pane.py      what is selected, beside the list
│       ├── clipboard.py         cut, copy and paste of files as `text/uri-list`
│       ├── settings.py          what is remembered between runs
│       └── dialogs/             questions (conflict, error, no trash, confirm delete, name
│                                taken, password) and the prompter that shows them, name
│                                (rename, new folder), rename_many, select, compress,
│                                details, properties, settings
└── tests/
    ├── core/                    copy, move, trash, cancel, failure, conflicts against tmp dirs
    └── integration/             island reporter against a fake bus, theme reader
```

## Components

### Startup (`app.py`, then `application.py`)

0. Ask to be the one process of the session (see "One process, and the desktop's file manager"). When another is, hand it the paths and end, before any of the application is set up or even imported: `app.py` imports Gio and the argument parser only, and `application.py`, with Qt and the windows, after the claim (`tests/test_start.py` holds it to that).
1. Parse arguments: zero or more paths or `file://` URIs (`%U` from the `.desktop` file). A file argument opens its folder with the file selected. A missing path opens home and shows a message.
2. `QGuiApplication.setDesktopFileName("filehouse")`. On Wayland this sets the app id, which the shell's `DockService.findToplevels` compares **exactly** against `StartupWMClass`. This is what gives point 3a for free.
3. Load the theme and apply the style before creating any window.
4. Open one window per path argument. With none, the tabs of last time in one window (see "The tabs of last time"), or home.

### One process, and the desktop's file manager (`integration/instance.py`, #38)

The app is meant to stand where Dolphin stood, so the desktop has to be able to hand it a folder.

- **One process for the session.** The first start owns `io.github.joonaskit.filehouse` on the session bus. A later start is told the name is taken (`RequestName` with "do not queue"), sends its arguments to the owner with `Open(uris, activation_token)` and exits, in under a fifth of a second (it was half a second while Qt and the windows were imported first) and before a `QApplication` is made. So there is one `JobModel`, one island client and one list of favourites however the app is started. The arguments go as URIs, because a relative path means something else in the other process. The owner opens **a window per folder** (not a tab: the first open question of the issue), and a home window when it was given nothing.
- **Without a bus, or with an owner that does not answer** within five seconds, the start goes on as a process of its own, as every start used to be: better a second process than no window.
- **`org.freedesktop.FileManager1`** is served by the same process, which is how other apps do "Show in folder". `ShowFolders` opens each folder; `ShowItems` opens the folder an item is in with the item selected (`locations.resolve_item`: a folder is an item too, shown in the one above it), one window per folder however many of its items were named; `ShowItemProperties` opens the properties dialog, without a window behind it. The name has one owner: when another file manager has it (Dolphin, while it runs), it keeps it, and this process is only the one process of this app (`Instance.file_manager` says which).
- **Started by the bus.** `data/org.freedesktop.FileManager1.service.in` becomes a service file in the user's `dbus-1/services`, which comes before the system's (Dolphin's), so a call with nobody running starts `filehouse --service`: no window of its own, and it ends after ten seconds if no call opened one. That it takes the place of Dolphin's is the choice made by installing it (the second open question). Two things found on a real session (Fedora, dbus-broker): the bus reads service files only when it starts or is told to reload, and it outlives a logout, so `install.sh` calls `ReloadConfig`; and Dolphin's own background process (`plasma-dolphin.service`, started by the first call that reached it) holds the name until it is stopped. With both dealt with, the broker logs that it ignores Dolphin's duplicate, and a `ShowItems` call starts `filehouse --service`.
- **The .desktop file** has `MimeType=inode/directory;`, so the app can be chosen as what opens a folder (`gio mime inode/directory filehouse.desktop`, or System Settings → Default Applications; `xdg-mime default` works too, with a harmless complaint about `qtpaths` on Fedora). It is not `DBusActivatable`: that would need the file to be named after the bus name, and the file's name is the app id (`filehouse`), which the shell's dock matches.
- **Coming to the front.** On Wayland a window may only take the focus with a token from whoever asked. The later start passes its `XDG_ACTIVATION_TOKEN` (or the X11 startup id), `FileManager1` callers pass theirs as `startup_id`, and the owner puts it in its environment before it shows the window, where Qt looks for it. Checked only as far as the token arriving; whether KWin then raises the window was not seen in a session.
- **The last window closing leaves the process running** (the third open question, decided the other way at first: it ended, and every start was a fresh one). A start is then a call to a process that has everything loaded: about 0.25 to 0.3 seconds from the launcher to the window's first paint, against 0.9 for a process of its own (and 0.55 for the first window of a process that never had one: the icon theme is read then). What it costs is the memory of a process without a window, about 140 MB resident and 70 MB of it its own (PSS), measured after five windows were opened and closed.
  - **A setting**, "Keep running without a window, so the next one opens sooner" (`settings.stay_running`, on by default). Off, the last window ends the process as it used to.
  - **Kept in one place:** whether Qt ends the app with its last window (`QApplication.quitOnLastWindowClosed`). `application.py` sets it from the setting and follows the setting's changes, and `main_window.quit_app`, which ends the process when the last job of a closed window has ended, asks it.
  - **`filehouse --quit`** (`Quit` on the bus, `Instance.ask_to_quit`) ends it: at once with nothing open, and otherwise it is no longer to stay, and ends with its last window or with the last job of a window that was closed over it. A new version of the app is not running until this was done, so `install.sh` does it.
  - **A first window is a start.** What applies "at the next start" (the density, and the scale and fonts of the theme) is read again when a window is opened with none open (`start_over` in `application.py`): with no widget made at the old sizes it is the same as a start.
  - **Started by the bus** (`--service`), the process stays as well, where it used to end after ten seconds without a call; with the setting off it still does.
  - **Seen in the tray** (`ui/tray.py`), as Steam and Discord are, for as long as the process is one that stays: with the setting off, or after `--quit`, there is no icon. A click brings the window forward, or opens one as a start without a path would; its menu has New window and Quit Filehouse, which closes the windows too (one with jobs running asks as it always does, and "Don't close" leaves everything as it was). It is a `QSystemTrayIcon`, which Qt registers as a StatusNotifierItem with the id `filehouse`, the category "application status" and the icon by its name, all checked on a session (with `system-file-manager`, before the app had an icon of its own): the category is what the shell's `AppIndicatorPill` shows an item for. Qt speaks to the tray itself, so this is not a D-Bus call of the app's, and without a tray nothing is shown.
  - **Started with the session** when the user asks for it (`integration/autostart.py`, "Start it in the background when I log in" in the settings, off by default: it is the memory of a process from the login on). It is an entry in the desktop's autostart folder, `filehouse-background.desktop`, running the command this process was started with and `--service`. Whether it is on is whether the file is there, so Plasma's own Autostart settings turn it off too. Only a process that stays is worth starting: with staying turned off the entry is taken away and the choice is greyed out.
  - **The app's icon** is the lighthouse of `icons/filehouse-d.svg`. `install.sh` puts it in the user's icons as `filehouse` (hicolor, scalable), the name the launcher entry asks for, and `style.app_icon()` gives it to the windows and the tray, or the icon theme's `system-file-manager` where it is not installed. `icons/filehouse-f.svg` is the design that was not chosen.
  - **In `application.py`** all of this is the class `Windows`: the windows and dialogs that are open, opening them, and whether the process goes on. `run()` makes the application and the services around it (the island, the notifications, the theme) and hands the rest to it, which is what lets `tests/ui/test_application.py` hold it to the above without a process of its own.
- No Qt in the module, so it can be asked before there is an application, and every call the app serves is answered before its handler runs: the caller waits for the reply, not for a window. The two calls a start makes itself (`RequestName`, and `Open` from a later start) are blocking, with timeouts: they decide whether there is to be an application at all.

### The Open and Save dialog of other apps (`integration/portal.py`, `ui/chooser.py`, #39)

Apps that use the desktop portal (every Flatpak, and Qt and GTK apps set up for it) ask `org.freedesktop.portal.FileChooser` for a file, and the portal hands the question to a backend. This app is one, so the dialog those apps open and save with can be this app's list, sidebar, favourites, search and preview.

- **The backend** (`integration/portal.py`, Gio and no Qt) serves `org.freedesktop.impl.portal.FileChooser` at `/org/freedesktop/portal/desktop` and owns `org.freedesktop.impl.portal.desktop.filehouse`. It is the one process of the session that does (`application.py` starts it only for `instance.primary`), and the name is asked for asynchronously after the windows are set up: a start is not held up for it. `OpenFile`, `SaveFile` and `SaveFiles` each become a `Request` (what was asked, read from the options one by one, so that one that is not as it should be is left out and not the call), given to `on_request`, and answered through `respond`, `cancel` or `fail`. Only the first answer counts.
- **A call is always answered.** An app's Open waits for it, so a call that nothing takes, a dialog that fails to open, and what is still open when the process ends are all answered as "ended" (2), and the answer is flushed to the bus before the process goes. The portal's `Request.Close` (the app went away) closes the dialog.
- **Only files of this computer are an answer** (`file://`), as the portal requires. What is selected in the recent files is the file it stands for; something that has no path (a remote place) is not chosen, and the dialog says so.
- **The dialog** (`ui/chooser.py`, `Chooser`) is a window of its own kind, not a mode of the browser window: a `Pane`, the `Sidebar`, the `PathBar`, the `SearchField` and the `PreviewWindow` (Space), put together again with a name field for saving, the app's filters and its own questions ("choices": a few options, or a yes or no) under the list. That is the answer to how much it shares with the main window (the issue's second question): the parts, and none of `MainWindow`, whose actions are all about doing things to files. The only thing the dialog makes is a new folder. The toolbar's rounded groups moved to `ui/groups.py` for both.
  - **Opening:** a double click or the button on a file chooses it; on a folder both go into it. One file at a time unless the app asked for several (`FolderView.set_for_choosing`), and of a selection of several only the files are the answer.
  - **Folders** (`directory`, and `SaveFiles`, which asks for the folder to save several named files in): only folders are listed, the button chooses the selected one, or the one that is open when none is, and a line beside the buttons says which.
  - **Saving:** the name is offered with the part before the extension selected. A file clicked in the list gives its name. A name that is a folder is gone into; a path ("sub/a.txt", "/tmp/a.txt", "~/a.txt") is that file, when its folder is there. **Saving over a file asks first** (the third question), with Cancel as the default; for `SaveFiles` the question offers "Keep both" too, which gives the taken names the first free one (`conflicts.free_name`), as the portal allows. The dialog writes nothing: the app does.
  - **Filters** (`core/filters.py`): a name and patterns, each a glob or a MIME type, and a file passes when any fits. Globs are matched whatever the case ("*.jpg" is asked for by apps that mean PHOTO.JPG too), a MIME type also for its subtypes (`Gio.content_type_is_a`) and as a family ("image/*"). Folders always show (`DirectorySortProxy.set_wanted`). The filter in use is told to the app with the answer.
  - **Return** is the list's and the name field's: the buttons are not the dialog's default, or Return on a folder would both go into it and choose it.
  - **Its own:** hidden files are shown for this dialog only, its size is remembered apart from the window's (`chooser/size`), and it starts where the app says, or where a file was last chosen (`chooser/folder`), or at home. The sidebar has no trash, and the recent files only for opening files. How a folder is shown (details or icons, the sort order) is shared with the browser, as it goes with the folder (#43).
- **Over the window that asked** (`integration/parent_window.py`). The portal names that window: `x11:<id>` or `wayland:<handle>`. On X11 Qt makes the dialog its child. On Wayland the handle is one of the xdg-foreign protocol, which Qt does not speak; KDE's KWindowSystem does, and has no Python binding (Fedora's `python3-kf6-*` leave it out), so its `KWindowSystem::setMainWindow(QWindow *, const QString &)` is called through `ctypes`, with a QString made by hand that points at text the module keeps. That depends on the layout of a QString in Qt 6 (checked: the call is made only on Qt 6) and on the library's symbol; without the library, or anywhere else, the dialog is a window of its own. When the app asked for a modal dialog and it could be attached, it is window-modal. Checked in a headless KWin with a GTK window's exported handle: the call goes through and nothing crashes, also for a handle that means nothing. On a real session (Plasma 6, Wayland) the dialog stays above the window that asked and gets the focus.
- **Whether the portal asks this backend is the user's choice** (the first question: worth it before the app is packaged, with a switch). `install.sh` always installs `data/filehouse.portal` (in the user's `xdg-desktop-portal/portals`, which the portal reads: seen with 1.22) and a service file for the bus name, which runs `filehouse --service`; neither changes anything by being there, since the portal file names no desktop to be used in. `./install.sh --portal` writes the user's `xdg-desktop-portal/<desktop>-portals.conf`: the desktop's own configuration with `org.freedesktop.impl.portal.FileChooser=filehouse;<desktop>` added, because the first configuration file found is the whole of it. `--no-portal` removes it. A configuration file the script did not write is left alone, and the line to add is printed. The portal reads both when it starts, so the script restarts it.
- **The portal asks for its backend when it starts**, not at the first dialog, and waits 25 seconds for one that does not come; after that it offers no file chooser at all until it is started again. Found on the first real install: a Filehouse from before the install was still running (its windows keep it past `--quit`), it held the app's name without being the backend, and the `filehouse --service` the bus started for the portal saw the name taken and ended. And with nothing of the app running the bus did not start it either, in the same second in which it was reading the service files the script had just written again. So `install.sh --portal` restarts the portal only once the backend's name is owned: it starts `filehouse --service` itself when nothing of the app runs, and with an old process in the way it says to close the windows and run it again. It also means that with the portal on, the app is started in the background at login.
- **What a broken backend costs:** the portal does not fall back to the next backend when the one it chose fails to answer, so a `filehouse` that cannot start means no Open dialog in the apps that use the portal, until `--no-portal`. A process started by the bus for a dialog takes about as long as any first start; with the app staying in the background (the default) the dialog is a call to a running process.
- **Checked** against the real `xdg-desktop-portal` (1.22) on a private bus with a temporary home: it finds the portal file in the user's data folder, picks the backend from the configuration, and `OpenFile`, `SaveFile` and `SaveFiles` asked of `org.freedesktop.portal.FileChooser` come back with what the dialog chose. And on a real session: VS Code, Firefox and a Flatpak app open and save with it.
- **Not done:** the dialog does not tell the recent files what was opened (the app that opens it does or does not), there is no narrow layout, and an app's `current_filter` is used but a filter is never guessed from the name being saved.

### Window title

`"<folder name> — Files"`, updated on every navigation. Home shows as `Home`, root as `/`. The KWin script feeds this to the shell's window switcher (#24, point 5).

### Browsing

- **`core/listing.py`** enumerates a folder with `Gio.File.enumerate_children` on a worker thread, in batches of about 200, asking only for the attributes the view needs (`standard::*`, `time::modified`, `unix::mode`, `thumbnail::*`). Large folders appear progressively.
- **`DirectoryModel`** holds one folder. It appends batches, then follows changes with a `Gio.FileMonitor` (created, deleted, renamed, changed). The monitor starts before the listing, so nothing made meanwhile is missed, and a name it saw deleted or renamed away is left out of the batches that come after (#69): a `name.part` renamed into place while the folder lists leaves no ghost. Sorting and the hidden-files filter are a `QSortFilterProxyModel` on top: folders first, then natural order by name, case-insensitive.
- Navigation keeps a back/forward history per window. Up goes to the parent.
- **The back and forward buttons of a mouse** go through that history. Over a list they are that list's, which in a split makes it the active one first (`FolderView.history_requested`): the views take the press themselves, since an item view would take it for a click on what is under it, and the second of two quick presses, which comes as a double click, is one more step. Anywhere else in the window a press that nothing took comes to `MainWindow.mousePressEvent` and is the active list's; the sidebar lets it pass for that.
- **`FolderView`** has a details mode (`QTreeView`: name, permissions, modified, size) and an icons mode (`QListView` in icon mode, with thumbnails). The mode and the sort order go with the folder: see "The view of each folder".
- Thumbnails: look up `~/.cache/thumbnails/{normal,large}/<md5 of URI>.png` and check its `Thumb::MTime`. On a miss, generate image thumbnails with `QImageReader` on a thread pool and write them back to the cache following the freedesktop spec, so Dolphin can reuse them. Other types show their MIME icon.
- **Sidebar:** Home, Desktop, Documents, Downloads, Pictures, Music, Videos, Trash, then mounted volumes from `Gio.VolumeMonitor`, with eject. Without GVFS installed the volume monitor only sees mounts, not unmounted drives; that is acceptable for v1.

### Opening files and hand-off

- Double-click or Enter opens with the default app (`Gio.AppInfo.launch_default_for_uri`).
- "Open with" lists `Gio.AppInfo.get_all_for_type(mime)`.
- "Open in Dolphin" (really: the default handler of `inode/directory`, skipping this app) is in the folder's context menu, for anything the browser doesn't do.

### Operations engine (`core/operations/`)

This is the part that has to be right. Every user action that changes files becomes a **Job**.

**Job kinds:** copy, duplicate, move, trash, permanent delete, create folder, rename. Restore from trash is a job kind too, but it is built in phase 5 together with the trash view, so it is designed against the view that triggers it. Rename and create folder are synchronous and fast; they don't need an activity, but they go through the same error type.

**Life of a job:**

```
 queued ──▶ planning ──▶ running ──┬──▶ done
                │            │      ├──▶ cancelled   (with how far it got)
                │            │      └──▶ failed      (with which file and why)
                │            ▼
                │      waiting for user  (conflict or error; back to running)
                └──────────▶ cancelled / failed
```

1. **Planning.** Walk the sources and build a flat list of steps (make folder, copy file, remove source), counting files and bytes. Progress is indeterminate during this phase. Planning also detects conflicts at the destination and the case of copying a folder into itself, which fails at once.
2. **Running.** Execute steps in order. Files copy with `Gio.File.copy(..., progress_callback, cancellable)` to a temporary name next to the destination (`.filehouse-<random>.part`) and are renamed into place once complete, without `OVERWRITE` unless the user chose Replace. A half-written file therefore never appears under the real name, a file being replaced stays whole until its replacement is, and the temporary file is always the job's own to remove. Where a folder cannot rename (some GVFS backends: MTP, WebDAV) the move of the temporary file fails with `NOT_SUPPORTED`; the copy is then written under its real name, without the detour for the rest of that folder, and a partial file it made is removed on failure or cancel (never one it was replacing; #70). Copies keep modification times and permissions (`ALL_METADATA`). Progress is bytes done over bytes planned, so one large file doesn't stall the bar.
3. **Finishing.** The job reports `done` only when every step finished. Anything else is `cancelled` or `failed`.

**Moves.** Call `Gio.File.move` with `G_FILE_COPY_NO_FALLBACK_FOR_MOVE` first, so Gio never does its own copy-and-delete. On the same file system that is an atomic rename. A folder whose destination is free and on the same file system (compared by `id::filesystem` while planning) is one rename step and counts as one item; any other folder is planned item by item, and so is a rename that turns out not to be possible at run time. If it fails with `NOT_SUPPORTED` (different file system), the job copies per file: each source file is deleted as soon as its own copy has completed, so the move needs no free space beyond the file in progress. Source directories are removed last, and only when empty (never recursively), so a folder holding skipped, failed or unmoved files stays in place. A cancelled or failed cross-device move therefore never loses data: each source file is either still in place or fully at the destination, but a folder may end up split between the two. Running the move again picks up the rest.

**Symlinks.** A symbolic link is copied as a link, never followed. The planner classifies items with `query_info(..., NOFOLLOW_SYMLINKS)`, so a link to a folder is one "copy link" step and the planner never walks into it. Copy uses `Gio.File.copy` with `NOFOLLOW_SYMLINKS`, so the new link has the same target text (a relative link may point somewhere else, or nowhere, from its new place). A same-device move renames the link; a cross-device move copies the link and then deletes the source link, never its target. Trash and permanent delete act on the link, not the target. A link counts as zero bytes for progress.

**Cancel.** Each job has a `Gio.Cancellable`. Cancelling:

- stops at the current file, deletes the partial destination file of the step in progress (the temporary file, so never a file that existed before), and leaves completed files in place. A copy that has already completed is still put in place, and in a move its source removed, so no step is left half done;
- reports `cancelled` with a summary: "Cancelled after 5 of 12 files" for copy and move, "Moved 5 of 12 files to the trash" for trash;
- is acknowledged in the UI within a fraction of a second, even mid-file, because `Gio.File.copy` checks the cancellable between chunks.

**Errors.** Every failure is an `OperationError(path, step, message)`, built from the `GLib.Error` with a readable message ("permission denied", "no space left on device", "file name too long"). On an error the job pauses in *waiting for user* and the app asks: Skip, Skip all, Retry, or Cancel. Without "Skip all" every error asks again. Skipped items are counted, and a job that finishes with skipped errors ends as `failed` with "Copied 10 of 12 files, 2 skipped" in its text, and Retry is offered for the skipped items (`Job.retry()` gives a new job for just those). An empty folder counts as one item, and a summary says "items" instead of "files" when it counts any folder. Items skipped by choosing Skip in the conflict dialog are intentional and don't count: a job whose only skips are conflict skips still ends as `done`.

**Conflicts.** When a destination exists: Replace, Skip, Keep both (append " (2)" before the extension), each with an "apply to all" option. Replace on a folder merges, it doesn't delete the existing folder. When the source and the destination are of different kinds (a file onto a folder, or a folder onto a file), that item fails with an `OperationError` naming the file: no merge and no replace. Skip and Keep both stay available. Conflicts are found during planning where possible, so the user answers before the long part starts.

**Asking the user.** `core/` cannot import Qt, so a job takes a prompt callback for conflicts, errors, a missing trash and the delete confirmation. It is called on the job's worker thread with a question object and either returns the answer (tests do this) or returns nothing and answers later from any thread with `question.answer(...)` (the UI does this in phase 3, after showing a dialog on the UI thread). The job does the waiting itself and watches its cancellable, so cancelling unblocks a pending question and a closed dialog can never leave a worker thread stuck. Without a prompt callback nothing is ever replaced, a failed item is skipped and reported, and a permanent delete is refused. A callback that raises fails the job.

**Trash and delete.** Trash uses `Gio.File.trash`. On a file system without trash support (some removable drives) the job stops and asks whether to delete permanently instead; it never falls back silently. Permanent delete always shows a confirmation with the item count: the delete job itself asks (`ConfirmDelete`) before it plans or touches anything, so no caller can skip it. Delete removes files one by one and each folder only once it is empty, never recursively and never through a link.

**Garbage collection is for the main thread.** Python's cyclic collector runs in whichever thread allocates enough, and collecting a Qt object's wrapper in a worker thread aborts the process (seen as intermittent crashes in the test suite, in a pooled thread, while a thumbnailer or a listing ran). `app.py` turns it off and collects every 5 seconds from a timer on the main thread; the tests do the same, collecting after each test. A new thread that makes Qt objects must still not leave them to be collected.

**Concurrency.** The queue runs up to two jobs at a time; others wait as `queued`. A job that is `waiting` for an answer does not count (#65): it gives its place back while it waits, so two questions nobody answers (in a window on another desktop) do not hold up a move to the trash. Once answered it goes on at once, even when that makes three for a while, rather than showing "Waiting to start" after the user has just answered it. The core starts no threads: `JobQueue` is given a `spawn` function by `qt_models/`, whose pool has 8 threads, so at most 8 jobs are under way (running or asking) and the rest wait their turn. A job cancelled while it is still queued reports `cancelled` at once and is never started. Only jobs under way are shown in the island, so this also keeps the app inside its limit of 8 activities per sender.

**Undo (#16).** Ctrl+Z, and Undo in the folder's menu, take back the last rename, move, trash, new folder or new file. `core/operations/undo.py` has what is kept and how it is taken back.

- **What is kept is what was finished.** A job that ends has a `Record` in `job.undo`, made from its steps, also when it was cancelled or failed halfway: the files a move really moved, the items a trash really trashed. A folder that went over whole is one pair of (where it is now, where it was); one that only partly did gives the pairs of what went. Left out, because they cannot be taken back: whatever replaced something (what it replaced is gone), what was deleted where there is no trash, and every permanent delete. A rename and a new folder or file, which finish at once, are told to the history by the window (`JobModel.remember`).
- **Taking back is an ordinary operation.** A move is taken back by a move job of each item to where it was, and a trash by a restore job (`Job.undo_of`): with progress, cancel, errors and Retry as any job, and by the same safety rules. Nothing is replaced: when something else has the old place now, the job asks as any move does, and with nobody to ask it fails for that file by name. A rename is renamed back and something that was made is removed, at once, as they were done (`undo.rename_back`, `undo.remove_created`); several renames go back all or none, through `rename_many`.
- **The world may have changed.** A file that is no longer where the action left it, a name that is taken, an item that is no longer in the trash: each is an error naming the file, and the rest is put back. The folder an item was in is made again when it went with the move.
- **A trashed item is found** by where it was and when it went there (`trash.find`, from `trash::orig-path` and `trash::deletion-date` of `trash:///`), when the undo runs and not when the trash did: the trash gives its own names, and Gio does not say which. Without GVFS there is no trash to list, and the restore fails saying so. Checked on a real session: two items trashed and restored, found at once.
- **Something that was made is removed only while it is empty**: a folder by a delete that is never recursive, which the file system refuses for one that holds anything, and a file only when nothing was written to it. This is a delete without the question a permanent delete asks (safety rule 5), on the ground that there is nothing in it to lose; anything a user has since put there is never deleted.
- **Decided** (the issue's open questions): a history, of the last twenty actions (`undo.LIMIT`), taken back from the last one; for the process and not a window, since jobs belong to the process, so one window takes back what another did; no redo, so the job that takes back is not kept (`job.is_undo`); and not kept past the end of the process, which with a process that stays is a long time all the same.
- **Not done:** a copy (taking it back is a permanent delete of the copies, which must ask), a duplicate, a link, a restore from the trash view, and changed permissions (the old modes would have to be remembered). After a folder that only partly moved is taken back, the folders the move made at the destination stay there, empty.

### Toolbar layout

One row of groups, each over the pane it goes with: back, forward and up over the sidebar; the path and the view group (details, icons, split) over the list; the search field and the settings button over the details pane. What is done to the selection (copy, move, rename, trash) has no buttons, in the toolbar or anywhere else: they were far from the pointer for something done to a file in the list, and are in the file's menu and on their keys. A narrow window had a bar of them under the list (the "action bar" of phase 6, below); it is gone too, and what is left of it is the details button: an "i" in the status bar, in the corner under the rail and in the middle of it, which opens the details sheet for the selection, or for the folder with nothing selected, and stays pressed while the sheet is open.

The groups end where the panes under them do. The navigation group is as wide as the sidebar, so the path starts at the list's left edge; the search field with the settings button is as wide as the details pane, so the field starts at its left edge and the button ends at its right. `MainWindow._align_toolbar` sets the two widths from where the panes are, whenever the sidebar or the details pane is resized, shown or hidden (the splitter is the user's to drag) and when the window is. It gives way where there is no room: the path keeps its least width, the sidebar's edge is kept first and then the details pane's, and the search field is never under 120 pixels. With no details pane (a split window, or one the user has put away) the field is 220 pixels at the right end, over the list. A narrow window, whose sidebar is a rail, leaves the groups at their own width, and its search field takes the place of the path while it is in use.

### The details pane can be put away (#35)

A wide window shows the pane of details beside the list unless the user has hidden it. One app setting (`view/details_pane`, on by default), the same in every window and remembered, like the private switch: `settings.changes.details_pane_changed` tells the other windows, and a window opens without the pane when it is off. It is toggled with **F11** (Dolphin's key for its information panel), the **Details pane** entry of the View menu, the button at the end of the view group in the toolbar, and a checkbox in the settings dialog under "New windows". The window's own `_pane_toggle` action is checked when the setting is on; `_details_wanted` is that, and a wide, unsplit window.

- **It is separate from the narrow window's details**, which are a sheet with an info button of their own and always work. The toolbar button is for the wide layout and goes with the split button in a narrow window.
- **A split window has no pane**, as before. The switch is kept: hidden stays hidden when the split ends or the window is wide again, and the same for shown.
- **Hidden, nothing is loaded for it.** `_update_details` skips a pane that is not wanted, so no details, thumbnail or folder size is asked for. The list takes the pane's width, the sizes the splitter had for it are kept for when it comes back, and the search field keeps its 220 pixels at the right end (see "Toolbar layout").
- **Not done:** a docked preview in the pane's place, as Dolphin's information panel has (the issue's fourth question): a feature of its own.

### Search (`core/search.py`, `ui/search_field.py`, #18)

A field in the toolbar, reached with Ctrl+F. What is typed is a list of words; a name matches when it has every one of them, in any order and any case ("2024 rep" finds "Report 2024.pdf"). Names only, no contents, and always by walking: no index is asked.

- **The folder itself is filtered at once**, in the sort proxy (`DirectorySortProxy.set_filter_text`), on every key.
- **The folders below are searched 250 ms after the last key**, on a worker thread (`search.search_below`), breadth first so what is near comes first, in batches like a listing. A link to a folder is found and never gone into, so the walk ends; a folder that cannot be read is passed over; `/proc`, `/sys`, `/dev` and `/run` are not searched. Hidden files and folders are searched only while they are shown, and a search follows the switch. It stops at 5000 results and says there are more. The trash and the recent files are only filtered.
- **A result is an entry of the open folder whose name is its path below it** ("docs/old/report.txt", with the file's own name as its display name). `Gio.File.get_child` resolves such a name, so `locations.child(folder, info.name)` and `entry_location` give the real file, and opening, renaming, trashing, dragging out, thumbnails, the preview and the details all work on results with the code that was there. `FileInfo.folder` is the path part, empty for an entry of the folder itself. The model adds results to its rows (`DirectoryModel.set_search`) and takes them away when the text changes.
- **Where a result is:** while a search is on, the Permissions column is "Folder" and shows the path below the open folder, sorts by it, and the tooltip of a row is the whole path; the status bar says "report.txt in docs/old" for a selected result and "3 found", with "searching…" while more may come. The menu of a result has "Show in folder".
- **Nothing watches the folders below**, so results are a snapshot: the search runs again when the jobs of the process have ended and after a rename, and reloading keeps it.
- **A search belongs to the folder it was typed in.** Each pane has its own, the field shows the active pane's, going to another folder ends it, and it is not a place in the history. Escape in the field ends it and returns to the list; Down or Return go to the list with the results kept.
- **In a narrow window** a button in the toolbar opens the field, which takes the place of the path for as long as it has the keyboard or a text.
- **For other programs: `filehouse --search TEXT`** (`core/find.py`, called from `app.py`). The shell's launcher has a file search and asks this app, so the walk and the rules of a match are in one place. It is not the application: it is answered before the claim to be the one process of the session, imports no Qt and no window (`tests/test_start.py` holds it to that), runs beside a running app and ends when it has answered, in about 0.4 s for a few results near the top of a home. `--root FOLDER` says where to look (home without it; a path, a `~` or a URI), `--limit N` when to stop (50 without it), `--hidden` includes hidden files and folders. What is in the root itself is a result too (`search_below(include_root=True)`): the app's list filters those, and a caller without a list has nothing that would. Output is one JSON object to a line, written and flushed as results are found, nearest first:

  ```
  {"path": "/home/me/docs/Report 2024.pdf", "name": "Report 2024.pdf", "folder": "/home/me/docs", "dir": false, "type": "application/pdf"}
  {"done": true, "more": false}
  ```

  `path` and `folder` are local paths, or URIs where there is none; `type` is the content type Gio guessed from the name. The last line comes when the walk has ended, and `more` says it stopped at the limit. A root that is no folder is one line, `{"error": "..."}`, and exit status 1. The one who asked may end the process at any time (the launcher does, on the next key), and a closed pipe is not an error. **The lines are an interface**: the shell reads them (`services/FileSearchService.qml` there), so a field is added and never renamed or taken away.
- **Not done:** searching contents or by type, asking an index (Baloo), results as a place in the history, and a wider Folder column (it has the width of the Permissions column, and cuts long paths).

### Duplicate and Create link (#41)

Both are in the menu of a file, after Rename. Neither has a key: Ctrl+D is the favourites', and Dolphin has none for them either (the issue's first question).

- **Duplicate** is a job (`JobKind.DUPLICATE`), so it has progress, cancel, errors and the island like a copy, of which it is one: each source is copied into the folder it is in, and the conflict that the source itself is (the name is taken) is answered "Keep both" before it is asked, so "report.pdf" becomes "report (2).pdf" and a folder "album (2)" with all that is in it. It asks nothing and replaces nothing; a name that turns up taken when the copy is put in place gets the next number. A link is duplicated as a link. Results of a search are each copied where they are.
- **Create link** makes a symbolic link (`quick.create_link`), immediately and without a job, like a rename. **Beside its target** the link is relative (the target's bare name), so a folder that is moved keeps both working, and it is called "Link to report.pdf". **In another folder** it is the target's full path, so the link can be moved about, and it has the target's own name (the issue's second question: relative survives a move of both, absolute a move of the link, and which one is likely follows from where the link is made). A name that is taken is never replaced: "Link to …", then "Link to … (2)". A link to a link points at the link.
- **Into another folder:** a drop with Ctrl+Shift, as in Dolphin, and "Link here" in the menu a drop with Alt opens. `MainWindow._drop_into` answers the drop as it does every drop, and makes the links itself.
- **Not offered** in the trash and in the recent files, and a link can only point to a file of this computer (the third question): a link to `trash:///…` would point nowhere a program could follow. Several links carry on after one that cannot be made, and say which.
- Not done: selecting the duplicate once the job has ended, and a hard link.

### Selecting (#42)

Beside the click, Shift, Ctrl, the rubber band and Ctrl+A:

- **Select matching…** (Ctrl+S, as in Dolphin; the folder menu, under Select all) asks for a text in a small dialog that says how many entries match as it is typed. With a `*`, a `?` or a `[` the text is a pattern for the whole name (`*.jpg`); without one it is the search field's words, in any order (`core/search.selector`). Both, then, and any case: the issue's first question. It **replaces** the selection unless "Add to what is selected" is ticked (the second).
- **Invert selection** (Ctrl+Shift+A, as in Dolphin; the folder menu).
- **Select all of this type**, in the menu of a file: every entry with its content type, and every folder for a folder.
- All three pick from what the list shows: a hidden file that is not shown, or an entry a search has filtered out, is not selected behind the user's back. `FolderView.select_where` makes one range for each run of rows, so a folder of thousands is a few ranges.

### Renaming several at once (#40)

F2 with several entries selected opens `ui/dialogs/rename_many.py` in place of the name dialog. The work is `core/operations/rename_many.py`.

- **The ways** (the issue's first question): find and replace in the name; a name and a number ("Holiday #" gives "Holiday 1", "Holiday 2", in the order of the list); upper case and lower case. The extension of a file stays unless "Change the extension too" is ticked; a folder has none.
- **Numbers** (the third question): each `#` is a digit, so `##` gives 01, 02; a pattern without a `#` gets the number at its end; and the count starts where the dialog says, 1 by default.
- **Every new name is shown first**, beside the old one, and checked on every key (`rename_many.check`): a name that cannot be used, a name two of them would get, and a name something else has, each shown in the danger colour with the reason, and any one of them **blocks all of it**. A name that another of the selected has now is free when that one gets a new name too (1 → 2, 2 → 3 is done 2 first); two that trade names are blocked, since no order does it.
- **All or none.** It is immediate, not a job (the second question): a rename is one call, and hundreds are done before a progress bar could be read. The check is advisory, as planning is for a job, so each rename is still the single rename that never replaces; when one fails (a file appeared meanwhile, a permission), those done before it are given their names back and the dialog says which file and why. If one of those cannot go back either, that is said with the count.
- Afterwards the renamed entries are selected.
- Not done: a job for a slow drive, trading names through a temporary name, and regular expressions.

### Folder automations (#87)

Rules attached to a folder that act on the files in it: *when a file matches these conditions, do these actions*. Decided in a review of the issue; not built yet. The later steps are #88 (run when a file appears), #89 (subfolders) and #90 (image metadata).

- **Where they run.** In the process that already stays without a window (#38), so there is no service of its own, and the shell only observes: a rule's work is ordinary jobs in `JobModel`, which the island already mirrors. `core/automations.py` has no Qt, so it could move into a service later.
- **Rules are data, not scripts.** A fixed set of conditions and actions, so a rule can be shown, previewed and checked. There is no "run a command" action.
- **What a rule touches.** Regular, visible files directly in its folder, in local folders only. Folders, symlinks and hidden files are left alone. A file modified in the last 10 seconds, or with a temporary name (`.part`, `.crdownload`, `.filehouse-*.part`), is skipped and picked up by the next run.
- **Conditions:** name (glob, contains, regex), extension or content type, size, modification age; all or any. **Actions**, in order on the same file: rename, move, copy, trash. A move or trash ends the processing of that file, and the dialog refuses actions after one. Rename templates have `{name}`, `{ext}` and `{date}` (the modification time until #90), not `{counter}`: it would differ on every run.
- **Order.** The rules of a folder are an ordered list, top to bottom for each file, with "Stop processing more rules" per rule. A run handles each file once, so the rules of a destination folder do not re-evaluate what the run moved in.
- **When they run.** By hand ("Run rules now" in the folder's menu, after the same preview the rule dialog shows), at process start and once a day. A coarse timer checks that 24 hours have passed since the last unattended run, so a laptop that slept through the time still catches up. Unattended runs act on every match without asking: rules are stateless, and the safety is the preview shown when a rule is saved, a rule becoming enabled only after it, and undo. Age conditions are checked on these runs and never on a file's arrival. Live watching is #88.
- **Safety.** Every action is an operations-engine job and the safety rules hold. A taken name always keeps both ("report (2).pdf"), and a rule cannot choose Replace. A missing destination folder is created, which the preview marks as new, and it is undone as a new folder is. Trash only: no permanent delete, and no fallback when the trash is unavailable, only an `OperationError` naming the file. A failed file does not stop the run; identical repeated failures notify once per process.
- **Undo.** One run is one entry of the history (#16), so Ctrl+Z takes back the whole run and a run of 40 files does not push out the user's own twenty.
- **Reporting.** Jobs in the panel and the island; a notification (#15) only when the run acted with no window open.
- **Where they are kept.** `rules.json` beside the settings file, by folder URI like `folders.json` (#43): written under another name and moved into place, checked when read. A rule whose folder is missing is kept and skipped without a message.
- **Switches.** Each rule has an on/off switch, and Settings has one "Run folder rules" switch, on by default, that pauses all of them. The rule dialog says that rules run only while the process does, and warns when staying in the background is off.
- **The dialog.** "Rules for this folder…" in the folder's menu: the ordered list, the editor, a preview of what the rules would do to the folder now ("report.pdf → ~/Documents/Invoices") and Run now. Saving is refused for a rule that cannot work: no actions, an invalid regex, a destination that is the rule's own folder or not writable, actions after a move or trash. The reason is shown beside the field. There is no overview of all folders with rules yet.
- **Tests** for `core/automations.py` run against `tmp_path`: matching, ordering and stop, keep both, a created destination, the temporary names and the 10 seconds, a failure in the middle of a run, and trash with no trash.

### Columns (#44, `qt_models/columns.py`)

The details view has eleven columns, one for each of `columns.KEYS` (a key's place there is its model column): Name, Permissions, Modified, Size, and Type, Created, Accessed, Owner, Group, and for what is in the trash, Original location and Deleted.

- **Which columns, and the cost** (the issue's first question). Every listing asks for `time::created`, `time::access`, `owner::user`, `owner::group` and `trash::deletion-date` as well. The times come from the one `stat` the listing makes anyway, and the names from the user database, which the C library keeps. Type is the content type already known, described by `Gio.content_type_get_description` (cached by type). A file system with no birth time gives a Created of 0, which is empty, never a wrong date.
- **Two sets.** The trash is chosen apart from other folders (`columns.TRASH` and `columns.FILES`; `view/columns_trash` and `view/columns_files`, a JSON list in the settings): the two columns that exist only there are on by default there, and not offered anywhere else. One choice for all folders of a kind, not one for each folder: the folders' own views (#43) keep the mode and the sort order.
- **The menu and the order.** A right click on the header lists the columns the folder offers, with a check on those shown, and "Default columns". Dragging a header moves the column. The name is always there and first (`setFirstSectionMovable(False)`; its item in the menu is disabled). Either way `FolderView.columns_chosen` carries the keys in order, `Pane` writes them with `settings.set_columns`, and every pane follows `settings.changes.columns_changed`. What is read is checked by `columns.choose`: an unknown or repeated key is dropped, so a file edited by hand cannot break the view.
- **Sorting** is by any column (`settings.SORTS` is `columns.KEYS`). A folder that does not offer the column that was chosen as the sort (Deleted, chosen in the trash) is sorted by name there.
- **A search** shows the Permissions column as "Folder", the folder a result is in. When the user put it away it is shown anyway for as long as the search lasts, and is not saved as a choice.
- **The narrow layout** (the issue's third question) shows the name and the first two chosen columns that are not dates; Modified, Created, Accessed and Deleted are dropped, so the default still shows Permissions and Size as before. Columns cannot be dragged meanwhile, so that what is saved is always the whole choice, not what fits.
- **Grouping** the list by type or date was split off into #54, and is not built.

### The view of each folder (#43)

The view mode (details or icons) and the sort order belong to the folder that is shown, not to the window.

- **The app's own choice** is `view/mode`, `view/sort` and `view/sort_descending` in the settings, so the sort order is remembered between runs. It is what a folder without a choice of its own is shown with.
- **A folder's own.** Changing the view or the sort order in a folder (the toolbar, the menu, a click on a column) is that folder's from then on, and only what was changed: a folder given icons still follows the app's sort order. `settings.choose_view` writes it, and `Pane._use_view` applies `settings.view_for(uri)` on every arrival. The app's own choice is then changed in the settings dialog only.
- **Per folder or one for all** is a setting (`view/per_folder`, on; the issue's first question, answered as Dolphin does: both). Off, a change anywhere is the app's and every folder follows; the folders' own choices are kept for when it is turned on again.
- **Where it is kept:** `folders.json` beside the settings file, by URI, written under another name and moved into place, read once per run. Not a `.directory` file in each folder: that litters them and fails on a read-only one. So a folder that is renamed loses its view. What the file holds is checked when read.
- **Not for the folders below** (the second question): Pictures as icons says nothing about a folder in it. It is the simpler rule, and Dolphin's.
- **500 folders** at most (the third question); past that the one whose view was changed longest ago is forgotten.
- **The way back:** "Use the default here" in the View menu, enabled where the folder has a choice of its own, and not there at all with one view for all.
- **The recent files** are the latest first whenever they are entered, as before (the fourth question). A sort chosen there is for that visit and is not kept; their view mode is kept like any folder's.
- **Every pane follows the settings** (`settings.changes.views_changed`), so another window, the other pane and the other tabs show a folder the new way at once, and the toolbar's two buttons say how the active pane shows its folder.

### The tabs of last time (#49)

- **Kept** when the last window closes (`MainWindow.closeEvent`, `settings.set_session`): each tab's folder, or the two of a split and which was the active one, and the tab in front. With several windows it is the one closed last (the issue's second question).
- **Opened** by a start without a path, when the process has no window (`app.py`, `main_window.session_to_open`). A start with a path opens that path in a window of its own, as before, and what was remembered is replaced when that window is the last to close (the fourth question). A later start without a path, while a window is open, opens a home window: the tabs of last time are in front of the user already.
- **A folder that is gone** is the nearest folder above it that is there (`locations.nearest_folder`); when nothing of it can be reached (a drive that is not mounted) the tab is left out, and a split with one such pane opens as the other alone. In the last tab, home. Nothing is looked at before the window opens, which could wait long on a share that does not answer (#61): the tabs open as they were, and a folder that cannot be listed is looked into then, on a worker (`Pane.fall_back_if_gone`). A pane that has moved on meanwhile stays where it went.
- **The trash and the recent files** come back as any folder does (the third question).
- **A narrow window** opens the active pane of each split, as narrowing a window leaves it.
- **A setting** (`window/restore_tabs`, "Start with the tabs of last time" in the settings dialog), on by default as in Dolphin, which this app stands in for (the first question). This reverses what "Tabs" first decided.
- Not kept: the history of a pane, the selection, the scroll position, and more than one window.

### Archives (`core/archives.py`, `core/operations/archive.py`, #19)

**Extract here**, **Extract to…** and **Compress…** are in the menu of a file. Each is a job (`JobKind.EXTRACT`, `JobKind.COMPRESS`), so it shows in the status bar, the panel and the island like a copy, with progress and cancel. `ExtractJob` and `CompressJob` are `Job`s with a `_work` of their own: an archive is one stream read or written from start to end, so they plan no steps and cannot be retried in the middle (`can_retry` is false).

- **libarchive is optional.** `archives.available()` is false without the binding or the library, and then the menu has none of the three. `archives.can_extract(content_type)` says which files get the Extract entries: archives that hold files under names, not a lone `.gz` or `.xz`.
- **Extracting makes one new item, reading the archive once** (#71). Listing it first would decompress a `.tar.gz` twice, so it is written as it is read into a hidden `.filehouse-….part` folder in the destination, and what is at its top level is known at the end: an archive with one top-level item gives that item, moved out of the hidden folder, and one with several gives the hidden folder itself, renamed after the archive (`pack.tar.gz` → `pack`), so nothing is spread over the open folder. That one item must be new: it is moved without replacing, and a taken name asks (`NameTaken`: Keep both or Cancel, never Replace); with nobody to ask, or on Cancel, the job fails or is cancelled and the hidden folder is removed, as nothing in it was ever given. Progress is the archive's own bytes read against its size, as the totals are not known before the end; the number of files is.
- **Paths from an archive are not trusted.** `archives.safe_parts` is the one place that decides: an absolute path, or one with `..`, is refused and reported as an error naming the entry, and the rest is still extracted. Folders on the way to a file are opened one component at a time with `O_NOFOLLOW` below a descriptor of the root, and files are made with `O_EXCL | O_NOFOLLOW`, so a link that the archive itself made earlier cannot be written through, and an entry listed twice does not replace the first. A link is made as a link and never followed; a hard link may only point at a safe path inside the root; devices, pipes and sockets are left out and said so. Set-user-id bits are dropped, the umask applies, and folders get their permissions last so a read-only one can be filled.
- **Cancel and failure.** Cancel removes the file being written and nothing else, and says how far it got ("Cancelled after 5 files": the total is not known yet). What was complete is put in place as above, from what was read so far, under a free name if its own is taken, as nobody is asked once the job has stopped; when no file was complete, the folders made on the way are removed. A damaged archive ends the job as failed, naming the entry it stopped at, and what was complete before stays. An entry that cannot be written is left out and reported, the job goes on, and it ends failed ("Extracted 10 of 12 files, 2 left out"); no space left stops it. An error names where the entry is once in place.
- **Passwords.** A protected archive asks (`Password`) when it comes to the first entry that needs one, and asks again while the password is wrong; then the archive is read again from that entry, past what was written already. With nobody to ask it fails. libarchive opens zip protection (the old ZipCrypto and AES); protected 7z and rar are not supported by it, and say so without asking.
- **Compressing** asks for a name and a kind (`ui/dialogs/compress.py`): zip, the default, or tar with gzip. It reads the sources and never changes them, stores links as links without walking into them, and writes to a temporary `.filehouse-….part` in the destination that gets its name only when the archive is complete, never onto something that is there (`NameTaken` again). Cancel removes the temporary file. A name that is not UTF-8, or a link pointing at one, is left out and named: libarchive does not store one alike in every format and locale. A file that cannot be read is left out and named, and the job ends failed with the archive holding the rest; when nothing could be read, no archive is made.
- **Where the destination cannot rename** (some GVFS backends through their FUSE path: MTP, WebDAV), the move into place fails with `NOT_SUPPORTED`, and what was made is copied into place instead (#84), still never onto anything: a taken name asks as above. The archive, or the one extracted file, is copied whole, and a partial copy is removed. An extracted folder is copied item by item: what cannot be copied is left out and named, the files counted are the ones copied, and no space left stops it with what was copied kept. The temporary file or hidden folder is removed after. Placing is not cancellable, moved or copied.
- **Local files only.** Both work on paths, so an archive or a destination that Gio reaches through GVFS fails with a message. That goes with #17.
- **Not done:** a lone compressed file (`.gz`, `.xz`), choosing a password when compressing, more formats for Compress (tar.xz, tar.zst, 7z), and selecting what was made once the job is done.

### Job model and in-app panel

`JobModel` holds all jobs from this process: one instance, made in `app.py` and given to every window. It is a `QObject` with signals (`job_added`, `job_changed`, `job_removed`, `question_asked`, `idle`), not an item model, because its observers are a panel of widgets and the island reporter. Worker threads report through queued signals; the model passes on every state change and otherwise at most about 10 updates per second per job. It also decides how long an ended job stays listed: finished and cancelled jobs for a few seconds, failed jobs until dismissed. Retry replaces the failed job with a new one for the failed items.

The **status bar** shows the running jobs as one line (since step 6d), and the **operations panel** is a strip at the bottom of the window that opens from that line, or by itself when a job fails or asks something. The panel shows every job with its title, text, progress and Cancel, and for a failed job Retry, Details (every error) and Dismiss. Each window has both and they all show the same jobs. Together they are the primary progress display. The island mirrors it.

**Questions.** One `Prompter` listens to `JobModel.question_asked` and shows one question at a time as a window-modal message box on the window that started the job, or on the active window when that one is gone. If the job is cancelled meanwhile the box closes; if the box's window is closed the question is asked again on another window. Closing a box without choosing cancels the job.

**Closing a window with jobs running.** Jobs belong to the process, so only closing the last window asks: "Keep running", "Cancel N operations" or "Don't close". With either of the first two the window hides and the process exits once every job has ended, because a cancelled job still has to stop and clean up. The island keeps showing progress meanwhile.

### Island integration (`integration/island.py`)

A small client of the activities API. It observes `JobModel` and translates; it holds no job logic. It has two parts: `IslandClient` does the D-Bus work and knows nothing about jobs, and `IslandReporter` maps jobs to activities and is tested against a stand-in for the client.

**Connecting**

- On start, check whether `org.quickshell.IslandActivities` has an owner and read the `Version` property. Use the API only when it is exactly `1` (the version this client knows). Otherwise stay silent; this is not an error.
- Watch the name with `Gio.bus_watch_name`. When the island appears (it restarted, or it was started after the app), call `Show` again for every job that is still running. When it disappears, drop all mapping state.
- Subscribe to `ActionInvoked(id, action)` and `Dismissed(id)`. Both are broadcast to the whole bus, so ignore ids this process didn't create.

**Mapping a job to an activity**

| Job | Activity |
| --- | --- |
| id | `job-<n>`, unique within the process |
| kind and count | `title`: "Copying 12 files", "Moving 3 items to Pictures", "Moving 40 items to the trash" |
| planning | `progress: -1` (indeterminate), `text`: "Preparing…" |
| running | `progress`: bytes done / bytes planned; `text`: current file name, its position and the speed ("film.mkv (3 of 12), 24 MB/s") |
| waiting for user | progress unchanged, `text`: "Waiting for you in Files" |
| queued | not published until it starts |
| over before it was seen running | not published, unless it failed: a paste that finishes at once doesn't flash in the island |
| — | `icon`: `edit-copy`, `transform-move`, `user-trash`, `edit-delete` by kind |
| — | `actions`: `[("cancel", "Cancel")]` while running; on failure `[("retry", "Retry"), ("show", "Show")]` |
| done | `state: done`, final title ("Copied 12 files"), empty `text`, `progress: 1.0` |
| cancelled | `state: cancelled`, `text`: "Cancelled after 5 of 12 files" |
| failed | `state: failed`, `error`: "report.pdf: permission denied" |

Rules from the API spec that the client must follow:

- **Throttle** progress updates to at most 4 per second per job, and always send the final state immediately. The island's limit is 20 calls per second per sender.
- **Cancel is a request.** On `ActionInvoked(id, "cancel")` cancel the job's `Gio.Cancellable`; the activity stays `running` until the job reports how it ended, and then the client sends that state. Never send `cancelled` before the work has actually stopped.
- **Show for a retry.** A finished activity can't go back to `running` with `Update`. Retry starts a new job, published with `Show` under the old id. `JobModel` adds the new job before it removes the failed one and says which job it replaces, so the reporter can hand the activity over; the failure stays on show until the new job starts.
- **Only what changed is sent.** The reporter remembers the fields the island has and puts only the differences in an `Update`. If a call fails, those fields count as not sent.
- **`Dismissed(id)`** on a failed job also dismisses it in the in-app panel, so the two stay in sync. Dismissing it in the app calls `Dismiss(id)`.
- **Errors from the island** are logged and never stop a job. `NotFound` means the activity is gone (dismissed or timed out): stop updating it. `LimitExceeded` means too many failed activities are still showing: dismiss the oldest failed one this app owns from the island (it stays in the app's own panel) and try once more. `RateLimited` means drop this update; the next one carries newer values anyway. A refused `Show`, and a refused final state, are tried again a few times, because nothing newer will follow them.
- **All D-Bus calls are asynchronous** and made from the UI thread, so a slow or hung island never blocks the app.
- **Crashes need no code.** If the app dies, the island turns its running activities into failures because the connection closed. That is why every job must hold its activity under the app's long-lived connection, not a short-lived one.

### Favourites (`integration/places.py`, `qt_models/favourites.py`, `ui/sidebar.py`, #26)

The user's favourite folders are a group in the sidebar, between Places and Devices. (The first version was a bar under the toolbar; the sidebar replaced it, with room for names, the narrow layout's rail, and one list to look at.) A click goes there, a middle click opens it in a tab, a menu changes it (the places and devices have one too: Open, Open in new tab, Open in split view, and Eject for a drive), and files dropped on one are moved or copied into it (a job, by the rules of any drop: `MainWindow._drop_into`). Files can be dropped on the other places too, and on the trash. A window made without a `Favourites` (the tests) has no such group.

- **Groups fold.** Each heading of the sidebar (Places, Favourites, Devices) is a click away from folding its group shut (a ▾ or ▸ in front of the name); which are folded is remembered between runs, for every window. The rail has no headings: a line stands between the groups, and nothing is folded there, so the favourites are icons with their names as tooltips in the narrow layout.
- **They are Dolphin's places.** The list is `$XDG_DATA_HOME/user-places.xbel`, shared with Dolphin and the file dialogs of KDE both ways: a place added here is in Dolphin's Places panel and the other way round, and the file is watched, so a change made there shows here a moment later. The favourites are the bookmarks that are not **system items** (Home, Desktop, the trash, the network, the recent files: this app has its own for those) and not **hidden**.
- **The file is edited as XML, not with `GBookmarkFile`.** It holds KDE's own notes on every entry (`ID`, `isSystemItem`, `IsHidden`, the group states, and `<separator>` entries that keep the place of a drive); a reader that knows only bookmarks would drop them when writing and break Dolphin's list. `PlacesFile` parses the file with `ElementTree`, changes only what it means to, and writes it back with the header, the `<!DOCTYPE xbel>`, the namespaces and the permissions it had, under another name and moved into place. A file it cannot read is left as it is. The one difference from what KDE writes is the order of the namespace declarations. A new place is made as KDE makes one (an `ID` of the time and a number one above the highest) and goes after the last bookmark, before the separators.
- **No file, no favourites.** If `user-places.xbel` is not there nothing is written: Dolphin makes it with the system items, and a file made here would be taken for a finished one. The empty group says "Not available yet" (its tooltip "Open Dolphin once"), and adding says the same.
- **Adding:** Ctrl+D (also the menu of the folder and of a folder in the list) puts the open folder in, and takes it out when it is there; folders dropped on the heading or on the hint of the group are added at the end, a file is not; and dropped on the **edge of a favourite** (its top or bottom quarter) they are added there, between it and its neighbour, with the same line that shows where a moved favourite would go. Dropped in the middle of a favourite, or on a place, they are copied or moved into it as any drop is. Only a drop of folders only is added: one with a file in it is a drop into the favourite, edges too. A folder that is a favourite already stays where it is. `Favourites.add` takes the place (`index`), which is two writes of the places file, an add and a move. Not the trash and not the recent files. An empty group says "Drag a folder here".
- **A favourite's menu:** Open, Open in new tab, Open in split view (not in a narrow window), Rename…, Move up and down, Remove from favourites.
- **Reordering by dragging.** A favourite is picked up and dropped between two others; an accent line shows the gap while it is over the group, and a drop on the heading puts it first. Only the favourites can be picked up. What is dragged carries `application/x-filehouse-favourite` (its ID) and **no file or URL**, so dropping it on a place, in a list or in another app cannot move the folder it stands for, and the sidebar accepts nothing but its own marker for it. The drop is answered as a copy, not a move: a list that is dragged from takes a move to mean the dragged rows are to be removed, and it is the favourites that change (`Favourites.move_to`, which is `PlacesFile.reorder`: the bookmark moves among the user's, and the system items and separators stay where they were), after which the sidebar is filled again.
- **Decided on the issue's questions:** the file is KDE's (shared with Dolphin); a sidebar group of its own, not merged into Places; no bar. Not done: a keyboard way to jump to one.

### Recent files (`integration/recent.py`, #14)

A file opened from the app (Enter, a double click, Open, Open with, the details pane's Open button) is told to the two lists the desktop has:

- **The freedesktop list**, `$XDG_DATA_HOME/recently-used.xbel`, which GTK apps, the portals and file dialogs read: written with `GLib.BookmarkFile` on a thread of its own (one write at a time; each reads the list, adds the file with its type and this app, and writes it again atomically), kept to the newest 500, and left untouched if it exists and can't be read.
- **Plasma's**, kept by KActivities, which Kickoff and Dolphin's Recent Files read: `RegisterResourceEvent` (accessed) and `RegisterResourceMimetype` on `org.kde.ActivityManager.Resources`, asynchronously and without starting the service. Checked on this system: the portal writes the first list, and Plasma's menus read the second, so both are needed. The shell's own "recent files" (the dock's downloads stack) does not read either yet.

**Privacy.** Nothing is told when:

- the user chose **"Don't remember opened files"** (the folder menu): a setting of the app, not of a window, remembered between runs, and the same switch in every window (`settings.changes` tells the others), as the browsers' private mode is not per window either. Turning it on or off says so in the status bar, and while it is on a "Private" sign sits in the status bar, which ends it when clicked. Thumbnails written to the shared cache are not covered yet.
- **Plasma's own setting** says no (System Settings, Recent Files, `kactivitymanagerd-pluginsrc`): "Do not remember" (`what-to-remember=2`), this app on the excluded list, or "only for specific applications" with this app not on the list. The values were read off the KCM's own files, not tested by changing the system's setting;
- the thing is a folder, a file that does not exist, a place off this computer, or in the trash. Only files.

It is silent, and can't stop a file from opening. A window made without a recorder (`recent=None`, the tests) tells nobody.

The switch is also in the settings dialog (#23). Not done: the thumbnail cache in the private mode.

**The Recent place.** The sidebar has "Recent" (before the trash) when GVFS has `recent:///`, which lists `recently-used.xbel`. It is read like any folder, and an entry is not a file but stands for one, so:

- `FileInfo` has `target_uri` (`standard::target-uri`) and `used` (`recent::modified`, an *int64*: reading it as uint64 asserts), and `locations.entry_location(folder, info)` is the file an entry means; everything that acts on a selection (open, copy, copy path, preview, properties, the details pane, thumbnails, drags) goes through it. The list's own name for an entry is a hash.
- The Modified column shows when the file was **used**, not when it changed, and a pane that enters Recent sorts by it, the latest first; leaving it brings back the sort that was chosen. (Folders still come first, as everywhere.) The type of an entry is guessed from its name, since the list gives none.
- **It is for looking at.** Cut, paste, rename, move, move to the other pane, new folder and file, permissions, trash and delete are off, and nothing can be dropped into it (`Pane.read_only` is the trash and the recent files). The Delete key takes the entry **off the list** ("Remove from recent files", which edits `recently-used.xbel` by `RecentFiles.forget` and leaves the file alone), and the list is read again. Plasma's own history is not touched: its entries for the file stay, and it is not what this view shows.
- **Clear recent files…** is in the menu of the list (not of an entry), after a question that says how many and that the files are not touched. It empties the freedesktop list (`RecentFiles.clear`), whichever app put an entry there, as GTK's own "Clear recent" does, and leaves an unreadable list as it is. **Plasma's history is not cleared**: `DeleteEarlierStats` on KActivities would wipe every app's, which is more than a menu item here says; the question points to System Settings, Recent Files, where it has its own buttons. A fuller "forget everything" belongs with the settings page (#23).
- The menu of an entry: Open, Open with, Show in folder, Preview, Copy, Copy path, Remove from recent files, Properties. Copying the files to the other pane works; moving them does not.
- What the list holds is what the freedesktop list holds: what this app, the portals (file dialogs) and GTK apps wrote. What KDE apps tell only KActivities (Dolphin) is not in it, though it shows in Plasma's menus.

### Notifications (`integration/notifications.py`, #15)

For a job that ends while the process has no window to show it (the user chose "Keep running" and closed the last one). `Notifier` calls `Notify` on `org.freedesktop.Notifications` asynchronously and is silent when there is no service; `NotificationReporter` observes `JobModel` and decides, holding no job logic. The island already shows jobs, and the shell's own rule is that activities are for work in progress and notifications for finished work that may have been missed, so the two are not both sent for the same thing:

| Island | Job ends | Notification |
| --- | --- | --- |
| not running | done | yes, with the job's summary ("Copied 4 items to Pictures") |
| not running | failed | yes, critical and without a timeout: the summary and the first error and how many more |
| running | failed | no: the island keeps a failure until it is dismissed |
| running | done | only if it ran 10 seconds or more (`LONG_JOB_SECONDS`), the island's "done" being gone after 4 |
| either | cancelled, or never ran | no: the user knows |

- **A window counts as there** when it is visible and not minimized. With one, the app's own status bar and panel say it.
- **Flushing.** The process ends as soon as the last job has, so `app.py` flushes the notifier's connection on `aboutToQuit`; a notification sent in the same moment still gets to the bus.
- **Not done:** an action on the notification ("Open folder"), and keeping the process alive after a failure to bring a window back when it is clicked. The process is gone by then, and a failure names the file and the error; running the operation again is the way back.
- Whether to notify at all and the 10 seconds are in the settings dialog (`settings.notify`, `settings.long_job_seconds`; `app.py` hands them to the reporter as functions, so a change counts at once).

### Settings dialog (#23, `ui/dialogs/settings.py`)

A modal dialog, opened with Ctrl+, or the gear button at the right end of the toolbar. A choice is stored as it is made; there is only Close. Sections: **Appearance** (density, which says it applies once every window has been closed), **Folders** (the app's view mode and sort order, for the folders without their own, and whether each folder keeps its own: the open windows follow these from the settings), **New windows** (whether a start opens the tabs of last time; hidden files and the details pane, which also change the window the dialog was opened from; whether the process stays without a window, and whether a login starts it), **Privacy** (the "Don't remember opened files" switch, followed live from the folder menu and the status bar, and Clear recent files…, which asks first) and **Notifications** (on or off, and the seconds a job must run before the island's user is told). The dialog holds no logic of its own: it reads and writes `ui/settings.py` and calls the window for what a window has to do. Not in it: the scale and fonts (the shell's), and anything the shell owns.

### Preview (`ui/preview.py`, `qt_models/previews.py`)

Space opens a window with a larger look at the selection, as Quick Look does on the Mac; Space closes it, and "Preview" is in the menu of a file. It is a tool window of the main window that follows the selection: the arrow keys, Home, End and Page Up/Down pressed in it are passed on to the list, so the selection moves and the preview shows what is selected now. With nothing selected it closes.

- **Pictures** (what `QImageReader` reads) are loaded on a worker thread, upright, to at most 4096 pixels on the long side (scaled while decoding) and 256 MB of file. Shown as large as fits and never distorted; a small one is enlarged at most twice. The line under it says the type, the size, when it changed and the picture's size.
- **Text** (`text/*`, and the types of the icons' "code" kind: JSON, XML, scripts and the like): the first 64 KB, in the fixed font, read-only, not wrapped. The encoding is told from the bytes: a byte order mark, then UTF-8, and a file that is not valid UTF-8 is read as Western European (cp1252), which any bytes are. A cut inside a character is not an error. A file with NUL bytes is not text whatever its name says, and shows "No preview for this type". A cut file says "the first 64 KiB" in the line under it. No syntax colouring. The text box never takes the keys, so the arrows move the list's selection as for a picture.
- **Documents** (PDF, where PySide6 has `QtPdf`): a page at a time, drawn on a worker thread with `QPdfDocument` at 1400 pixels high or more, on white, since a page is drawn without its paper. The line under it says "Page 2 of 14". Page Up and Page Down turn the pages and stop at the first and the last; for anything else those keys are still the list's. Each document opens at its first page, and a page zooms and moves like a picture. Without `QtPdf` a PDF is a picture of its first page, where Qt's image plugin has it.
- **Zoom and pan** for a picture: the wheel (around the pointer), + and −, and 0 to fit again; from a quarter to 16 times what fits. Drag to move a picture larger than the window; one that fits stays in the middle. Each new picture starts fitted. Fitted, it is scaled once with the smooth filter; zoomed, the painter scales it.
- **Full screen:** Return or a double click toggle it. Escape leaves full screen first and closes the window the second time; Space closes it at once. A window closed in full screen opens as a window again.
- **Anything else** shows the type's tile with "No preview for this type"; a folder says "Folder"; a selection of several says how many; a file that is not on this computer, or a picture that can't be read, says so.
- **The same file is not loaded twice** (by path, date and size), because the window is asked again every time the list's state changes.
- Opens at 80 % of the main window's size, and where the compositor puts it: not remembered (a tool window is placed by KWin on Wayland).
- **In a narrow window it is a sheet** at the bottom, as the details are: a second `PreviewWindow` made with `sheet=True`, in a `Sheet` of 45 % of the window's height, with the file's name in the line under it (there is no title bar) and a close button. It takes no keys: the list keeps the keyboard, so the arrows move the selection as ever, and the window has actions for the sheet that are enabled only while it is open (Escape to close, and Page Up and Page Down while it shows a document of several pages). One sheet at a time: opening the preview closes the details and the other way round. The wheel zooms and a drag moves, as in the window. A double click opens the same file in the preview window in full screen, and leaving the full screen closes that window again, the sheet being as it was. A preview that is open changes form when the window crosses the narrow width.
- **Space while a name is typed.** Typing at the list jumps to a name (Qt's keyboard search), and Space is the preview's key. `FolderView` notes when a letter of a name was typed; for as long as Qt waits for the next letter (`keyboardInputInterval`, 400 ms), Space is a part of the name ("my file") and not the shortcut: the view accepts the shortcut override and hands the space to the search. After that Space opens the preview again.
- **Several files** (`show_items`): with more than one selected the preview shows one of them, with "2 of 5" first in the line under it, and Left/Right/Up/Down (or the two buttons that appear beside the line, also in the sheet) go through the selection and stop at its ends; they do not move the list's selection. A new selection starts at its first file, the same selection again (the window asks on every change of the list's state) keeps its place. Home, End and Page Up/Down are still the list's, or the document's pages.
- **Video and audio** (`ui/media.py`, `Media`): `QMediaPlayer` with `QVideoWidget`, where the system's PySide6 has `QtMultimedia` (optional like `QtPdf`: without it these types say "No preview for this type"). Made the first time a media file is shown. It opens paused at the start, because the preview follows the arrow keys and a list of songs would otherwise play every one; a row of controls under it has play and pause, a slider and the time, and a click on the video plays and pauses. Audio shows the type's tile in place of a picture. It stops, and lets go of the file, when another item is shown, the selection is several things or nothing, and the preview closes. A file the backend can't play says "Could not play it". The tests that need the backend skip where `QtMultimedia` is missing.

### Thumbnails for any type (`qt_models/thumbnails.py`, #20)

Asked for every regular file on a local folder that is painted in the icons view, in this order, on a worker thread:

1. **The cache**, whatever the file's type, so what Dolphin made for videos, PDFs and the rest is shown. A cached thumbnail counts when its `Thumb::MTime` is the file's.
2. **The fail marker** (`thumbnails/fail/filehouse/<md5>.png`, as the spec has it, with the file's `Thumb::MTime`): a file that was tried and could not be made is not tried again until it changes.
3. **Qt**, for the types `QImageReader` reads. That includes PDFs on a Qt with its PDF image plugin, which draws the first page (the system's PySide6 has it); a file above 64 MB is left alone.
4. **The desktop's thumbnailers**: the `thumbnailers/*.thumbnailer` files in `$XDG_DATA_HOME` and `$XDG_DATA_DIRS` (the user's first, a file name seen once), for the types in their `MimeType`, when their `TryExec` and program exist. The command's `%i` (path), `%u` (URI), `%o` (output file, in a temporary folder) and `%s` (128) are filled in; it is run without a shell, with `QProcess` used blocking on the worker thread (Python's `subprocess` forking from one of Qt's pool threads crashed the process now and then), in a session of its own, with no input and no output kept. The result is scaled to 128, written to the shared cache like any other, and the temporary folder removed.

Only two thumbnailers run at once, in a pool of their own (so slow ones never take the threads the image thumbnails need). Each has 15 seconds, then its process group is killed and the file counts as failed. A file above 4 GiB is not handed to one. A failure of the program (a non-zero exit, no output, an output that is not an image) is remembered; a file nothing could be tried on is not.

Not done: `QtMultimedia` for video, and KDE's thumbnail plugins through KIO: without `ffmpegthumbnailer` or another thumbnailer installed, only what Dolphin has put in the cache shows for video. Remote locations and the large (256 px) size are still not thumbnailed.

### Theme (`integration/theme.py`, `integration/style.py`)

- Port the shell's `examples/read_theme.py`: start from complete defaults, overlay `$XDG_CONFIG_HOME/quickshell-island/theme.json`, ignore unknown fields, fall back per field, and use defaults for a missing or newer `version`.
- `style.py` maps it to Qt:
  - `QPalette`: the window's layers (see "Window colours" under phase 6) → Window, Base, AlternateBase and Button, `textPrimary` → WindowText and Text, `textSecondary` → PlaceholderText, `accent` → Highlight, `onAccent` → HighlightedText.
  - Font: the first installed family from `fonts.families`, at `max(8, round(px * scale.font * scale.ui))`.
  - Sizes (row height, icon sizes, paddings) defined in unscaled pixels and multiplied by `scale.ui`.
  - A short stylesheet for corner radii (`radius.small` for buttons and rows, `radius.medium` for panels) and the operations panel. Keep it short: a large stylesheet fights the platform style.
  - `danger` and `success` for failed and finished jobs.
- **Live updates (#13).** `integration/theme_watcher.py` watches the theme's directory with `QFileSystemWatcher` (the shell replaces the file atomically, which a watch on the file alone does not survive), and the file too, for a change in place. Before the shell has ever run it watches the directory above, to see the directory come. A change is read 200 ms after the last event, and `style.apply(app, theme, live=True)` runs again.
  - **Applies at once:** colours (the scheme, accent, palette, layers) and the corner radii. The palette and the stylesheet are set again; the icons are drawn in colours, so what holds one (the actions, the tab close buttons, the sidebar, the details pane) makes it again when `style.themed.changed` is emitted. The file list asks the model for its icons each time it paints, and the model's icons are cached per `style.generation`.
  - **The font is the shell's everywhere.** `style.apply` sets the app font and then, after the style sheet, the same font for the kinds of widget a platform theme gives a font of its own (Plasma's theme does, in points: the lists, the toolbar's buttons, the status bar, the tooltips, which were Noto Sans at 12 to 13 pixels beside the shell's Cantarell). After the style sheet because a new style makes Qt read the platform theme's fonts again. The sidebar also sets the app font on itself, so a heading read from `font()` has a size in pixels before and after the window is shown.
  - **Applies at the next start**, or at the next window opened with none open, which is the same to a process that stays (see "One process"): `scale.ui`, `scale.font` and the font families. Sizes and fonts are set in the widgets when they are made (`setFixedSize`, margins, row heights, `QFont`), so a live apply keeps the scales and fonts of the running theme; a window with half its parts at the new scale would look worse than the old scale. This is the answer to the issue's open question.
  - **Density (#28).** Compact or comfortable (the default), one choice for every window, stored as `view/density` and chosen in the folder menu's View > Density. It applies at the next start or the next first window, like the scale. `style.set_density()` runs before `style.apply()`, and `style.dense(key)` gives the unscaled sizes that depend on it: the file list's row padding and icon, the icons view's icon and cell, the sidebar's row, heading and icon, the toolbar's control height and group padding, the standalone button's height, the tabs' and menu items' vertical padding, the details card's margin and spacings and the operations panel's row padding. Text keeps its size.
  - A theme file that is gone or cannot be read leaves the look as it is; the defaults are for starting without one. The two job titles painted in the failure colour (`job_status`, `operations_panel`) are set again with the next update of the job.
- Window decorations stay server-side (KWin draws them). Matching the frame to the shell is tracked in shell issue #56.

### Motion (`integration/motion.py`, #11)

The shell has a catalogue of its animations (`docs/ANIMATION_CATALOGUE.md` in the quickshell repo). Most of it is about what a window of widgets does not have: pills that morph, a dock that popups grow out of, things that spring up from nothing. What carries over is a slide, a progress bar's fill, and the rule that nothing has a duration of its own.

- **The values are a copy.** They are in the shell's `Theme.qml` and not in `theme.json`, so `motion.py` has them, with the catalogue named as the source. Nothing in `ui/` has a duration or an easing: it asks `motion.ms()` when an animation starts. Should the shell ever export them, this module is the one place to read them.
- **A slide takes 160 ms** (`SLIDE_MS`, the shell's `animDurationPopover`) with `OutCubic`, opening and closing alike. Not the 360 ms of the shell's own slides, and no overshoot: that is for a pill that morphs, and a file browser is to stay out of the way. The shell leaves the overshoot out of its full-width bar for the same reason.
- **A progress bar fills in 300 ms** (`PROGRESS_MS`, `animDurationProgress`) with `OutQuad`, as the shell's bars do.
- **What slides** (`ui/slide.py`):
  - the sheets of the narrow window (details and preview) and the operations panel, by their height (`Slide`): what is above gives and takes the room;
  - the details pane of the wide window, by its width in the splitter (`SideSlide`), when the user shows or hides it (F11, the button, the setting). It may be narrower than its minimum only while it moves, and comes back as wide as it was.
- **What does not:** the details pane coming and going with a split or a narrow window (the list is changing under it already), the switch between the wide and the narrow layout (it happens while the window is being resized), and hover and selection colours (a stylesheet cannot animate, and painting them by hand is a lot for a fade). The settings have checkboxes, so there is no switch with a knob to slide.
- **The bars** (`ui/progress_bar.py`, in the status bar and the panel's rows) fill from one report of a job to the next. A bar starts where the job is, without a fill, when it appears or was busy. It is behind the job and never ahead of it, and it says nothing about done: only the job's state does (safety rule 1).
- **The desktop's animation speed** scales every duration: `AnimationDurationFactor` in the `[KDE]` group of `kdeglobals`, the user's file before the system's, 1 without one. At 0 nothing moves: a panel is shown or hidden at once and a bar jumps. It is read again when it is two seconds old, so a change in System Settings applies while the app runs. The tests run with it at 0 (`tests/conftest.py`), and the tests of the animations turn it on.
- **In a window that is not on show** nothing is animated either.
- **`settled`** of a slide means that what is beside it has its new size, with or without an animation: the list scrolls to what is selected then.

### Drag and drop and clipboard

- Dragging out sets `text/uri-list`. This is what the dock's future `DropArea` in #32 will accept.
- Dropping in: copy by default across devices, move on the same device, Shift and Ctrl to force move or copy, Ctrl+Shift to link, and a menu (Move, Copy, Link) when Alt is held, matching Dolphin.
- Cut, copy and paste use `text/uri-list` plus `application/x-kde-cutselection` for cut, so pasting between this app and Dolphin works both ways.

## Implementation plan

Each phase ends with something usable and checked by hand. The core gets tests from phase 2 on.

**Status:** phases 0 and 1 are implemented as v0.1 alpha. Phases 2, 3 and 4 are implemented and tested, and phase 4 was checked by hand with the shell running. Phase 5 is implemented and tested; mounting and ejecting were only tested against stand-ins for drives. Phase 6, the new design, is implemented and tested, steps 6a to 6g; the custom title bar was never part of it (issue #9).

### Phase 0: skeleton and integration basics

- Project layout, `pyproject.toml`, `.desktop` file, `app.py`.
- Path arguments, `setDesktopFileName`, window title from the folder.
- Theme reader and style, with defaults.
- Spike: Gio D-Bus signal delivered inside `app.exec()`.

**Done when:** `filehouse ~/Downloads` opens a themed, empty window titled "Downloads — Files"; the shell's dock shows it as running under its own icon; the launcher's window switcher lists it; the window still opens with `theme.json` deleted.

### Phase 1: browsing

- `listing.py`, `DirectoryModel` with batches and a file monitor, sort and hidden-files proxy.
- Details view, path bar, back/forward/up, sidebar places.
- Open with the default app, Open with, Open in Dolphin.
- Keyboard: Enter, Backspace (back, as in Dolphin), Alt+Left/Right/Up, Ctrl+L, Ctrl+H, F5, type-to-select.

**Done when:** browsing a folder of 10 000 files stays responsive; files created elsewhere appear without a refresh.

### Phase 2: operations engine (core, no UI)

- Job, planning, copy, move (same device and cross device), trash, delete, conflicts, errors, queue.
- pytest suite against temporary folders, covering at least:
  - copy and move of nested folders, with byte-based progress;
  - cancel mid-file removes only the partial file and reports how far it got;
  - a cross-device move cancelled or failed halfway leaves every source file either in place or complete at the destination, a folder may be split between the two, and a source directory that still holds files is not removed (use a tmpfs or a second temporary mount when available, otherwise force the copy-and-delete path);
  - a permission error names the file;
  - copying a folder into itself fails during planning;
  - Keep both picks a free name;
  - copy and move of a file link, a folder link, a broken link and a link to a parent folder: each is copied as a link, the planner doesn't recurse into it, and the target is never read or changed (including after a cross-device move deletes the source link);
  - `done` is never reported when any step failed or was skipped after an error; the job ends as `failed`. Items skipped through a conflict choice don't prevent `done`.

**Done when:** the suite passes; no UI yet.

The tests use `/dev/shm` as the second file system when it is one, and also always run the cross-device cases with renames forced to fail. They run with `HOME` and the XDG directories pointed at a temporary folder (`tests/conftest.py`), so the trash they use is not the real one.

### Phase 3: operations in the UI

- `JobModel`, worker glue, operations panel.
- Copy, cut, paste, drag and drop, delete key (trash), Shift+Delete (confirm, then delete), rename (F2), new folder.
- Conflict and error dialogs; the close-with-running-jobs prompt.

**Done when:** everything in phase 2 can be done from the window, with progress, cancel and errors visible in the panel, and the app works the same with the shell stopped.

### Phase 4: island integration

- `island.py`: version check, name watching, mapping, throttling, actions, dismissal sync, error handling.
- Test the reporter against a fake activities service on a private bus (`dbus-run-session`), or against a fake transport object. Both are done: the reporter against a stand-in client, and the client against a stand-in service on a `dbus-daemon` started by the test, so the real session bus is never used.
- Manual checks with the shell running:
  - a copy shows in the island and ends as "done";
  - Cancel in the island stops the copy and shows "Cancelled after N of M files";
  - a permission error shows as failed with the file name, stays until dismissed, and Retry works;
  - `kill -9` the app mid-copy: the island shows it as failed;
  - restart the shell mid-copy: the activity reappears;
  - three copies at once: the island shows one with `+2`, and the expanded view lists all three.

**Done when:** all of the above behave as described.

### Phase 5: polish for daily use

- Icons view with thumbnails.
- Mounted volumes and eject in the sidebar.
- Trash view with restore and empty. Restore needs the trash location and each item's original path, and is built here as a job, not in phase 2.
- Properties dialog (size, counts, dates, permissions read-only).
- Remember window size, view mode and hidden-files setting.

**How phase 5 was built**

- **Trash.** The trash is the GVFS location `trash:///`, which shows the trash of every drive in one place and tells where each item was (`trash::orig-path`). Restore is its own job kind: one step per item, moved back whole with `Gio.File.move`, the folder it was in made again if it is gone. A restore never replaces what is there now (Skip and Keep both only). Deleting from the trash is the ordinary delete job with its confirmation, planned as one step per item because the trash removes an item with everything in it; "Empty trash" is that job for every item. In the trash nothing can be pasted, renamed or created, the Delete key deletes for good, and a drop on it trashes. Nothing inside a trashed folder can be restored or deleted by itself. Without GVFS there is no Trash place.
- **Trash in tests.** The real `trash:///` belongs to the user, so tests never open it. The engine is tested with a plain folder standing in for the trash, and the window with the `locations` functions pointed at such a folder. Trash, restore and delete were checked against a real GVFS trash once, by hand, in a session with its own bus and home.
- **Volumes** (`qt_models/volumes.py`) follow `Gio.VolumeMonitor`: mounted volumes, then drives that could be mounted. Clicking an unmounted one mounts it and goes there. The context menu ejects, or unmounts what can't be ejected. A window showing a drive that goes away returns to home. Encrypted drives that need a passphrase are not handled: the error is shown.
- **Icons view.** A second view on the same model and selection (Ctrl+1 icons, Ctrl+3 details, as in Dolphin). Thumbnails are only loaded in this mode, for images Qt can read on local files, and only for items that are painted. `qt_models/thumbnails.py` follows the freedesktop thumbnail spec (`normal`, 128 px, `Thumb::URI` and `Thumb::MTime`, written under a temporary name and moved into place), so the cache is shared with Dolphin both ways.
- **Properties** (Alt+Return) reads one file with `core/properties.py` and adds up folders on a worker thread, links not followed. For several items it shows the totals. It is read only.
- **Remembered** in `$XDG_CONFIG_HOME/filehouse/filehouse.conf`: window size (saved when a window closes), view mode and whether hidden files are shown. Since then also the sort order and the tabs of last time, and beside it `folders.json` with the view of each folder.

### Phase 6: the new design

The design is six pictures in `desing/` (not in git): a wide window with three panes, and a narrow window with its menus and sheets. This phase brings the app to that design. It is split into steps that each leave the app usable, ordered so that restyling what exists comes first and new behaviour after.

The custom title bar in the pictures is **not** part of this phase. It is a decision of its own, in issue #9; every step below works under KWin's normal title bar.

**Theme in this phase.** Everything the design needs is already in the shell's `theme.json` (`docs/THEME.md` in the shell repo), so nothing has to be added to the theme:

- **Icon tiles** take their colours from `colors.palette` (green, blue, orange, yellow, red, purple, cyan, indigo), which the theme provides "for categories and decoration". The app decides which place and which kind of file gets which palette colour; the colour values are the shell's. A tile is the palette colour at low opacity with the icon in the full colour.
- **Selection, focus and the progress bar** use `colors.accent`, which the user picks in the shell's settings, with `colors.onAccent` on top of it. Status colours come from `success`, `warning` and `danger`, never from the palette.
- **Corners** use `radius.small`, `medium` and `large`. These follow the user's corner roundness setting, so the window only looks as round as the pictures when the shell is set to "Round". The app does not hard-code the pictures' radii.
- **Light scheme.** The shell has an experimental light mode and exports `scheme` with matching colours. Nothing in the app may assume a dark background: translucent overlays (hover, dividers) are derived from the text colour, not from white.
- **Reader.** `integration/theme.py` currently defaults `palette` to empty and keeps whatever entries the file has without checking them. It gets the full default palette and the same colour check as the other colours, so the tiles work with no theme file.

**Window colours.** Changed after 6c: the first design put the cards on the theme's `background`, which is pitch black in the dark scheme and harsh behind a whole window. The window now uses the colours of the shell's own windows (its clipboard history is the model), all derived in `style.layer` from two theme colours:

- **window** and the list: the theme's `surface`.
- **card** (sidebar, details, toolbar groups, menus): `surface` with 4 % of the text colour mixed in, and a hairline border of the text colour at 6 %. The border is what sets a card apart.
- **raised** (the Open button, a checked toolbar button, text fields, the progress bar's track): `surface` with 10 % of the text colour.
- The selected row is the accent at 22 %.

The theme's `background` and `surfaceRaised` are no longer used. Because every layer is `surface` plus some text colour, a light scheme gets darker cards on a light window without anything said about it.

**6a. Restyle what exists.** No new behaviour.

- Toolbar as rounded groups: back/forward/up, the breadcrumb, the action icons (copy, move, rename, trash), the list/grid switch.
- Sidebar as a rounded card: a coloured icon tile per place, and a "Devices" heading above the drives.
- List rows: taller, with an icon tile per file and a rounded highlight for the selection.
- Menus: an icon per entry, shortcuts on the right, "Move to trash" in the danger colour.
- Status bar text: "11 items · Dock.qml selected".

**Done when:** the wide window matches the picture apart from the title bar, the details pane and the columns.

**Done.** How it was built:

- `integration/style.py` has the helpers the rest of the phase uses: `palette_color`, `radius`, `overlay` (the text colour at an opacity), `icon` (a theme icon in one colour) and `tile` (an icon on a rounded tile in a palette colour). Icons are drawn once as ordinary pixmaps; a custom icon engine was tried and dropped, because Qt can outlive Python at exit and then calls into an engine that is gone. `style.generation` goes up with each applied theme, so caches of icons know when to start over. An icon is made only in the sizes it can be shown at, because each size is a search of the icon theme and a drawing of its own (about a millisecond), and a window has some seventy icons: made in all eight sizes they were more than half of the time a start took. `icon` makes the sizes of a button (up to 24, scaled), or with `menu=True` the one size of a menu entry; the window gives its actions their icons together (`_draw_action_icons`), once it is known which of them sit on buttons. `tile` makes every size, for the file kinds that lists, the details and the preview all show, or with `side` the one size a place in the sidebar has, and that size again for the pixels of a dense screen.
- `qt_models/icons.py` sorts content types into eight kinds (folder, image, video, audio, archive, document, code, other), each with a palette colour. The sidebar maps places to colours the same way.
- The breadcrumb starts at Home for anything in the home folder.
- The toolbar has no "show hidden files" button any more; it is in the folder menu and on Ctrl+H.
- Menus are made with `ui/menus.py`, which makes the popup's rounded corners show.
- Not done: "Move to trash" has a red icon, but its text is not red. Qt's menus have no per-entry text colour, and a custom entry widget was not worth it yet.

**Fixed after 6a:** the breadcrumb's hover highlight was cut off, because the path bar was only as tall as its text field while the breadcrumb buttons were taller. Icon buttons and breadcrumbs now share one height, `style.CONTROL_HEIGHT`, and the path bar is that tall.

**6b. Columns and list menus.**

- Columns Name, Permissions (`rwxr-xr-x`), Modified, Size. Type moves to the details pane.
- Modified as "Today, 10:42", "Yesterday", a weekday within the last week, then a date.
- Size of a folder as "24 items", counted in the background after the folder is listed, so listing stays fast.
- Folder menu: Select all, Sort by, View.

**Done when:** a folder of 10 000 files still lists as fast as before, and the counts fill in afterwards.

**Done.** How it was built:

- **Permissions** come with the listing (`unix::mode`, which the listing's `stat` already has: 10 000 files list in the same time with and without it) and are written as `ls` does, in the system's fixed-width font. The columns beside the name are in the secondary text colour.
- **Modified** (`qt_models/dates.py`): "Today" with the time, "Yesterday", the weekday for two to six days ago, a date for anything older or in the future. A week ago is a date, because its weekday is today's. Times and dates are written in the user's locale; the words and weekday names are English like the rest of the app.
- **Folder counts.** `core/listing.py` has `count_children`: what is directly in a folder, shown and hidden entries apart. `DirectoryModel` starts one count task when the listing has finished, for all folders in name order; it reports a few times a second, and each report is one `dataChanged`. A folder that can't be read shows nothing. The count includes hidden entries when the list shows them. Measured on 10 000 folders: listed in 0.9 s as before, all counts in 0.9 s more, with no longer stall of the UI than the listing itself causes.
- **Keeping counts current.** The folder's monitor says when a subfolder appears or changes its attributes, and that subfolder is counted again (changes are gathered for 300 ms, and only one count runs at a time). It does **not** say when something is put into or taken out of a subfolder. So all folders are counted again when the last job ends and when the window becomes active. In between, a count can be out of date until a reload.
- **Sorting.** The Size column sorts files by size and folders by their count, folders still on top. Sorting by size while the counts come in moves the folders as they arrive.
- **Folder menu**, in the order of the picture: New folder, Paste, Select all; Sort by, View, Show hidden files; Open folder in Dolphin, Properties. "Sort by" offers Name, Modified and Size (Modified and Size start with the newest and the largest) and Ascending or Descending; clicking a column header still works, also for Permissions. Select all is on Ctrl+A. The trash's menu gets Select all, Sort by and View as well.
- The sort order was not remembered then. It is now, for the app and for each folder: see "The view of each folder".

**6c. Details pane.**

- A pane on the right for the selection: icon, name, type, modified, size, owner, the permissions grid (read, write, run for owner, group, others) and an Open button.
- With nothing selected it shows the open folder; with several items, their count and total size.
- It reuses `core/properties.py` and the folder measuring from phase 5. The Properties dialog stays for now and is removed once the pane covers everything it shows.

**Done when:** selecting a file shows its details without opening a dialog.

**Done.** How it was built:

- `ui/details_pane.py` is the third part of the window's splitter, a card like the sidebar. It is given the open folder and the selected entries and decides by itself what to show. Asked again for what it already shows, it does nothing; an entry whose size, date or mode changed counts as new, so a selected file that changes is read again (the window looks for that at most five times a second). A single file's icon is replaced by its thumbnail (up to 128 pixels, rounded, 56 in the sheet) once the same engine as the list's has one, from the cache or made; the icon stays until then, and an answer for a file no longer shown is dropped.
- **Reading** is `core/properties.read` on a worker thread (`qt_models/details.py`). What is on show stays until the answer is in, and only the answer to the last question is used, so going down a list with the arrow keys neither blinks nor blocks.
- **One item:** tile, name, type (or "Link to …"), modified, size, owner as "you · group" when it is the user's own, the mode in octal, and the grid. The third column of the grid reads "Open" for a folder. Each dot says in a tooltip what it stands for ("Group may not write it").
- **A selected folder** is added up in the background as in the Properties dialog: its size, and "Contains 12 files, 3 folders". The count is cancelled when the selection changes.
- **Several items:** "3 items", their total size and "In all 5 files, 2 folders", added up the same way. No dates, owner or permissions.
- **Nothing selected:** the open folder, with how many items the list shows in place of a size. It is **not** added up: that would walk the whole drive for "/" on every visit. Its full size is in Properties.
- **Open** does what Enter does on the item. It is hidden for the open folder and for several items.
- The grid is a display. Its dots become switches, or not, with step 6g (open question 3).
- The Properties dialog stays (open question 5). It still has what the pane leaves out: accessed and created dates, the location, where a trashed item was, and the byte count.
- A new window is 1080 by 660, as in the picture, to make room for the pane.
- **Corners that fit.** A Qt stylesheet draws square corners when a radius is more than half the height of what it rounds. With the shell set to "Round" (`radius.large` 21 at scale 1.1) that made the Open button a plain box. `style.radius_within(name, height)` gives the theme's radius, but never more than half the height; the Open button and the toolbar's groups and buttons use it, and have a fixed height to go with it. Anything low that gets a theme radius must use it too.
- Not done: below about 800 pixels of width the list's columns no longer fit beside the pane and scroll sideways. The pane can be dragged shut with the splitter; the real answer is the narrow layout of step 6e.

**6d. Jobs in the status bar.**

- The status bar shows the running job as one line: title, progress bar, percentage; with several jobs, the count.
- Clicking it opens the existing operations panel, which stays the place for cancel, retry, details and for failures. A failed job or a waiting question opens the panel by itself.

**Done when:** a copy shows in the status bar as in the picture, and nothing the panel does today is lost.

**Done.** How it was built:

- `ui/job_status.py` is a button at the right end of the status bar, hidden without jobs. The item count moved to the left end, where temporary messages take its place while they show.
- **What the line says.** One job running: what it does without where to ("Copying 4 items"), a bar and the percentage. Several: "3 operations" and how far they are together, by bytes when all of them count bytes, otherwise the mean, with a job that is still being planned counted as not started. None running: "1 operation failed" in the danger colour if any did, otherwise how the last one ended ("Copied 4 items"), for as long as `JobModel` keeps it.
- **Never 100 %.** While a job runs the line shows at most 99 %: all bytes written is not done, only the job's state says that (safety rule 1).
- **The panel is closed until it is wanted.** Before this step it showed whenever there was a job. Now a click on the line opens and closes it, and its header closes it. It opens by itself when a job goes into `failed` or `waiting`, once per time it does: closed again by the user, it stays closed until something new needs them. When the last job is gone the panel is closed again for the next one.
- Nothing the panel did is gone: cancel, retry, details and dismiss are where they were, one click further away for a job that runs without trouble.

**6e. Narrow window.**

- Below a width to be chosen, the window changes layout: the sidebar becomes an icon rail, the breadcrumb shortens to "… › parent › folder", the action icons move to a floating bar at the bottom, and the details pane becomes a sheet that slides up.
- A "⋯" button opens the folder menu.

**Done when:** resizing the window across the width switches between the two layouts without losing the selection or the place in the list.

**Done.** How it was built:

- **The width** is 800 pixels, unscaled (`NARROW_BELOW` in `ui/main_window.py`), and the layout follows the window's width with no setting to force one (open question 4, decided). A window can't be made narrower than 460 by 360, which is what the narrow toolbar needs.
- **One window, two arrangements.** Nothing is rebuilt on the switch: the same list, model and selection stay, and each part is told to change its form. That is why the selection and the scroll position survive it.
  - `Sidebar.set_rail`: icons only, at a fixed width, names as tooltips, a line where the "Devices" heading was.
  - `PathBar.set_short`: "… › parent › folder". The "…" opens a menu of the folders above.
  - The toolbar loses Forward (Alt+Right still works) and its action group, and gains a "⋯" button for the folder menu.
  - The list loses its Modified column. The status bar says what the list no longer has room for: "Dock.qml · rw-r--r--", and the job line is one word, "Copying".
- **The action bar** (removed since: see "Toolbar layout") was a rounded group centred under the list, with Copy, Rename, Move to trash, Details and "⋯" (the selection's menu, which opens upwards). It is there only while something is selected. It is part of the layout, not laid over the list: floating as in the picture, it would hide the last rows, and whatever is selected there.
- **The details sheet** (`ui/sheet.py`) is a second `DetailsPane` in a compact form: icon beside the name, a close button, the mode spelled out ("rw-r--r-- · 644") and no Open button. The Details button in the action bar opens it, and its close button, the same button again or Escape closes it. It slides by animating its height, pushes the list up, and the list then scrolls just enough to keep the selected entry in view. While it is open the action bar is hidden and the sheet follows the selection.
- Going back to a wide window closes the sheet and puts the splitter back as it was.
- Not in this step, because they are steps of their own: Move and the lock button in the action bar (6f, 6g), and the search button in the toolbar (later).

**6f. Small additions from the menus.**

- Copy path.
- Open terminal here (the default terminal; hidden when none is found).
- New file.
- A usage bar per drive in the sidebar.
- "Move" in the toolbar: move the selection to a folder that is picked.

**Done.** How it was built:

- **Copy path** (Ctrl+Alt+C, as in Dolphin) puts the paths of the selection on the clipboard as plain text, one per line; with nothing selected, the open folder's. It is text, not files, so it can't be pasted back as a copy.
- **Open terminal here** (Shift+F4, as in Dolphin). `integration/desktop.py` picks the terminal: the one set in Plasma's settings (`TerminalApplication` in `kdeglobals`), then `$TERMINAL`, then `xdg-terminal-exec` and a list of known ones. It is started with the folder as its working directory, which every terminal opens its shell in, so no terminal needs its own command line option. The entry is hidden when no terminal is found and where there is no local folder to start in (the trash, remote places). In the menu of a folder in the list it opens that folder.
- **New file** makes an empty file with a typed name, the way New folder does, and never replaces anything (`quick.create_file`). Templates, which the submenu arrow in the picture suggests, are left for later (open question 6, decided).
- **Usage bars.** `qt_models/volumes.py` asks each mounted volume how full it is (`filesystem::size` and `free`, asynchronously) when the drives change, when the last job ends and when the app starts. The sidebar draws a bar under the drive's name, in the accent colour and in the warning colour from 90 % full, with "12 GiB free of 500 GiB" as the tooltip. The rail has no room for the bar and keeps the tooltip.
- **The system's drive** is now the first device, named after the system ("Fedora Linux") and leading to "/", as in the picture. Without it most machines would list no device and show no bar.
- **Move to…** is in the toolbar, the action bar and the file menu. It asks for a folder with the desktop's own dialog and starts an ordinary move job, so conflicts, cancel and errors behave as for any move.
- **Menus** now follow the pictures' order. Folder: New folder, New file; Paste, Select all; Sort by, View, Show hidden files; Copy path, Open terminal here, Open folder in Dolphin, Properties. File: Open, Open with, Open folder in Dolphin; Cut, Copy, Paste; Rename, Move to, Move to trash, Delete permanently; Copy path, Open terminal here (folders); Properties.

**6g. Changing permissions.**

- The four presets from the pictures, from the Permissions submenu and the lock button: Private 600, Shared 664, Make executable 755, Read-only 444.
- It changes files, so it goes through the core with the same error type and tests as every other operation.

**Done.** How it was built:

- `core/operations/permissions.py` has the presets and `PermissionsJob` (`JobKind.PERMISSIONS`, started with `JobModel.set_permissions`). It was first a call that finished at once, run on a thread of its own; a selection can be thousands of files, and then it had no progress, no cancel, and was in neither the panel nor the island (#63). As a job it is queued like any other, counts the items as it goes, stops between two items when cancelled ("Cancelled after 2 of 4 items"), asks Skip or Retry for an item that could not be changed and can retry the skipped ones as a job of their own. It ends "Changed the permissions of 3 items", "… of 1 file, folders left as they were" or "… of 2 of 3 items, 1 failed". It plans nothing: each item is one change. It is not taken back by Undo.
- **Folders** (open question 2, decided) get the preset with the "open" bit wherever there is a "read" bit: Private 700, Shared 775, Read-only 555. "Make executable" says nothing about folders and leaves them alone; it is greyed out when only folders are selected.
- **Only the items themselves.** Nothing inside a selected folder is changed. Applying a preset to everything in a folder would be a job of its own, with progress, cancel and partial failure, and is not built.
- **Links are never followed**, as in every other operation: a link has no permissions of its own, so it is reported as an error, without a question (Retry would not help) and what it points to is not touched.
- **Special bits.** A folder keeps set-group-id and sticky, which say who new files in it belong to and who may delete from it. A file loses set-user-id and set-group-id: such a file made writable by its group would be a hole.
- **In the window.** A Permissions submenu in the file menu, and the lock button behind a line in the narrow window's action bar, which opens the same menu upwards. Each entry shows the mode it gives (the folder's mode when folders are selected, none when files and folders are selected together) and the one the selection already has is ticked.
- **Afterwards** the panel says what was changed, as for any job, and a failed one keeps its row with the errors by name ("What went wrong"). The list and the details pane update by themselves, through the folder's monitor.
- **The grid stays a display** (open question 3, decided). Permissions are changed through the presets only.

**Left for later** here, and built since: search (see "Search") and Compress (see "Archives").

**Open questions for this phase**

1. ~~Colours of the icon tiles~~ Decided: from the theme's `colors.palette`, see "Theme in this phase". Which colour goes with which place and file type is fixed in the app.
2. ~~Folder presets~~ Decided: folders get 700, 775 and 555, "Make executable" leaves them alone, and nothing inside a folder is changed. See 6g.
3. ~~The permissions grid~~ Decided: a display. Permissions change through the presets. See 6g.
4. ~~The width at which the layout changes~~ Decided: 800 pixels, and no setting to force a layout. See 6e.
5. **Properties dialog.** Remove it when the details pane is done, or keep it for several items?
6. ~~New file~~ Decided: an empty file with a typed name. Templates later. See 6f.

### Later

Each has an issue, where what it takes and what is still to be decided is written down.

- Live theme updates (file watch on the theme directory): #13, built; see the theme section.
- Custom title bar: a decision of its own, #9 (and #21 comment in the shell repo).
- Recent files written to the freedesktop recent-files list, for the shell's launcher (shell #24, point 4): #14, built; see below.
- A notification for a job that finished while the window was hidden (shell #24, point 7): #15, built; see below.
- Undo for rename, move and trash: #16, built; see "Undo".
- Remote locations through GVFS: #17.
- Search: #18, built; see "Search".
- Archives, extracting and compressing, each a job so it reuses the activity mapping: #19, built; see "Archives".
- Video and PDF thumbnails: #20, built; see below.
- The file chooser portal, to be the Open and Save dialog of other apps: #39, built; see "The Open and Save dialog of other apps".
- Folder automations, rules that rename and move files: #87, decided but not built; see "Folder automations". Then #88 (watching), #89 (subfolders), #90 (image metadata).
- Duplicate and Create link (#41), selecting by pattern (#42), renaming several at once (#40), the view of each folder (#43) and the tabs of last time (#49): built; each has a section above.

Also open, though never on this list: packaging as a Flatpak (#7), localization (#10) and tabs (#22). The split view (#12) and tabs are built; see below.

### Split view (#12)

F3, the toolbar button or View → Split view shows two folders side by side. Each is a `Pane` (`ui/pane.py`: model, sort proxy, list, history, selection, error state); the window has one or two and an active one, `_pane`. The active pane is the one whose list was last clicked or given the keyboard, and it is framed in the accent colour while the split is on (a list always has a border around it, in the colour of the cards' borders: it says where the list is, however few rows it has). The path bar, back/forward/up, the title, the actions, the status bar and the sidebar's highlight all follow it. A drop, a click or a menu in a list makes it active first, so a drop lands in the folder of the list it was dropped on, with the rules for drops as before (move on the same file system, copy across).

**Open in split view** is in the menu of a folder, right below Open in new tab (not in a narrow window, which cannot split). It shows that folder in the other pane: the window is split first if it is not (a split that is not remembered for new windows, as F3's is), and in a window that is split already the other pane goes there, with Back returning it to where it was. The pane the folder was chosen in stays where it is and stays the active one.

Decided, from the issue's open questions:

- **Narrow layout:** no split below 800 pixels. Narrowing the window closes the split (the active pane stays) and it is not brought back by widening; the toggle is disabled there.
- **Details pane:** hidden while the split is on; the sizes of the window's parts are put back when it closes.
- **Path bar:** one, following the active pane.
- **Tabs:** out of scope, #22. A tab is meant to be a `Pane` too.
- **Shared between the panes:** hidden files. The view mode and the sort order go with the folder each pane shows (#43), so two panes on one folder show it the same way; the toolbar's two view buttons say how the active pane shows its. A new second pane opens in the active pane's folder.
- **Copy and Move to the other pane:** Shift+F5 and Shift+F6 (F5 is Reload), also in the item's context menu while the split is on. Disabled when either pane is in the trash.
- **Remembered:** whether the split is on (`view/split`), for every new window. The second pane opens in the first pane's folder. Closing the split because the window got narrow does not forget it. A window that opens the tabs of last time (#49) has their splits and their folders instead.

### Tabs (#22)

Ctrl+T, "New tab" in the View menu, or a middle click on a folder (and "Open in new tab" in its menu) opens a folder in a tab. A `Tab` (`ui/tab.py`) holds one pane or two with its active one, so each tab has its own split; the window holds a list of them and shows one at a time (`_tab`, and `_pane` as its active pane). Everything that follows the active pane follows the shown tab.

- **Keys:** Ctrl+W closes the tab, or the window when it is the last; Ctrl+Tab and Ctrl+PageDown go to the next tab, Ctrl+Shift+Tab and Ctrl+PageUp to the previous. A new tab from Ctrl+T or the menu is shown at once; one from a middle click opens behind.
- **The bar** is shown only with two or more tabs, also in the narrow layout (it scrolls, and the tabs elide). Tabs can be dragged to reorder. The title is the folder's name, the tooltip its path.
- **Dropping on a tab** puts the files in that tab's active folder, by the rules of any drop, without switching to it.
- **Shared by all tabs:** hidden files. Folder and history are each pane's own, and the view mode and sort order go with the folder (#43).
- **Remembered between runs** since #49, which reversed the first decision (that bringing tabs back surprises more than it helps): see "The tabs of last time". It can be turned off.
- **Narrowing** the window closes the split of every tab, as for one tab. A drive that goes away takes every pane that was on it to home.

### What phase 3 added to the plan above

- **New folder and rename** (`core/operations/quick.py`) are immediate and never replace anything. Their dialog stays open and shows the reason when a name can't be used.
- **Drops.** The window always answers a drop with "copy" and does the work as a job, so the app the files came from never removes anything itself. A move of files onto the folder they are already in, and a folder dropped on itself, are ignored.
- **Paste.** A cut is used up by its paste (the clipboard is cleared). On quitting, the app lets go of clipboard contents that are still its own.
- **Shortcuts.** Cut is Ctrl+X only: the platform's list for Cut includes Shift+Delete, which is permanent delete here.
- **`DirectoryModel`** drops the answer to a file query that is older than that file's removal. Without this, a file written under a temporary name and renamed into place, which is how this app and many editors write, could stay listed as a ghost.

## Open decisions

1. ~~Widgets or Qt Quick~~ Decided: Qt Widgets.
2. ~~A job with skipped items~~ Decided: `failed` when items were skipped after an error; conflict skips still end as `done`.
3. ~~Closing with running jobs~~ Decided: closing the last window offers "Keep running", "Cancel N operations" and "Don't close"; the process exits when the jobs have ended.
4. ~~The app's real name and app id~~ Decided: `filehouse`.
5. **Custom title bar:** issue #9.
6. The open questions listed under phase 6.
