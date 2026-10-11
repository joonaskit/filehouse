# File browser: architecture and implementation plan

A small file browser for KDE Plasma (Wayland), written in Python and Qt. It is a companion app to [Quickshell Island](https://github.com/joonaskit/Quickshell-Dynamic-Island) but runs as its own process and works without the shell.

It is not meant to replace Dolphin on features, stability or security. It covers the behaviour that matters to its author, integrates with the shell, and hands off to Dolphin (or the default file manager) for everything else.

Working name in this document: **filehouse** (app id `filehouse`). Rename before the first release; the `.desktop` file, the Wayland app id and `StartupWMClass` must change together.

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
| Thumbnails | Freedesktop thumbnail cache, plus `QImageReader` for images | Shares the cache with Dolphin. Video and PDF thumbnails come later |
| Tests | pytest (core), pytest-qt (a few UI tests) | The core has no UI and can be tested against temporary folders |

On Fedora: `dnf install python3-pyside6 python3-gobject`. For development, a venv created with `--system-site-packages` sees both.

**Open decision: Widgets or Qt Quick.** Widgets is the recommendation above. Qt Quick would make it easier to match the shell's look and animations, at the cost of building the views. If matching the shell's feel matters more than the effort, revisit this before phase 1, because it decides the whole `ui/` layer.

**Spike in phase 0: Gio inside the Qt event loop.** Qt on Linux normally runs on the GLib event dispatcher, so Gio callbacks and D-Bus signals attached to the default main context are delivered by Qt's own loop. Confirm this with the PySide6 build in use (a Gio D-Bus signal subscription must fire while only `app.exec()` runs). If it does not hold, fall back to iterating the GLib main context from a `QTimer`, or to QtDBus for the activities client.

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
- **One source of truth for job state:** `JobModel`. Both the in-app operations panel and the island reporter observe it. The island is a second view of it, never the only one, because the app must work without the shell.

### Project layout

```
island-files/
├── ARCHITECTURE.md              this document
├── README.md
├── pyproject.toml               entry point: island-files = island_files.app:main
├── data/
│   ├── island-files.desktop     Exec=island-files %U, StartupWMClass=island-files
│   └── icons/
├── src/island_files/
│   ├── app.py                   argument parsing, QApplication, desktop file name, first window
│   ├── core/
│   │   ├── locations.py         path/URI handling, places (home, Downloads, …), display names
│   │   ├── listing.py           batched directory enumeration, file info records
│   │   ├── errors.py            OperationError: which file, which step, readable message
│   │   └── operations/
│   │       ├── job.py           Job, JobState, JobProgress
│   │       ├── plan.py          expand a request into concrete steps, count bytes
│   │       ├── copy.py          copy and move (same-device rename, cross-device copy+delete)
│   │       ├── trash.py         trash and restore
│   │       ├── delete.py        permanent delete
│   │       ├── conflicts.py     conflict detection and resolution policy
│   │       └── queue.py         runs jobs with a concurrency limit
│   ├── qt_models/
│   │   ├── directory_model.py   QAbstractItemModel over listing + Gio.FileMonitor
│   │   ├── job_model.py         list model of jobs, fed by worker threads
│   │   └── workers.py           thread pool glue
│   ├── integration/
│   │   ├── theme.py             theme.json reader (port of the shell's examples/read_theme.py)
│   │   ├── style.py             theme → QPalette, fonts and a small stylesheet
│   │   ├── island.py            activities client: JobModel → activities, actions → jobs
│   │   └── desktop.py           open, open with, default file manager hand-off
│   └── ui/
│       ├── main_window.py
│       ├── sidebar.py           places and mounted volumes
│       ├── path_bar.py          breadcrumbs, editable on click or Ctrl+L
│       ├── folder_view.py       list and grid modes, delegates
│       ├── operations_panel.py  in-app list of jobs, with cancel, retry and details
│       └── dialogs/             conflict, confirm delete, properties, rename
└── tests/
    ├── core/                    copy, move, trash, cancel, failure, conflicts against tmp dirs
    └── integration/             island reporter against a fake bus, theme reader
```

## Components

### Startup (`app.py`)

1. Parse arguments: zero or more paths or `file://` URIs (`%U` from the `.desktop` file). A file argument opens its folder with the file selected. A missing path opens home and shows a message.
2. `QGuiApplication.setDesktopFileName("island-files")`. On Wayland this sets the app id, which the shell's `DockService.findToplevels` compares **exactly** against `StartupWMClass`. This is what gives point 3a for free.
3. Load the theme and apply the style before creating any window.
4. Open one window per path argument (one window when there are none).

Every launch is a new process. There is no single-instance logic and no D-Bus name in v1 (#24, point 6).

### Window title

`"<folder name> — Files"`, updated on every navigation. Home shows as `Home`, root as `/`. The KWin script feeds this to the shell's window switcher (#24, point 5).

### Browsing

- **`core/listing.py`** enumerates a folder with `Gio.File.enumerate_children` on a worker thread, in batches of about 200, asking only for the attributes the view needs (`standard::*`, `time::modified`, `unix::mode`, `thumbnail::*`). Large folders appear progressively.
- **`DirectoryModel`** holds one folder. It appends batches, then follows changes with a `Gio.FileMonitor` (created, deleted, renamed, changed). Sorting and the hidden-files filter are a `QSortFilterProxyModel` on top: folders first, then natural order by name, case-insensitive.
- Navigation keeps a back/forward history per window. Up goes to the parent.
- **`FolderView`** has a details mode (`QTreeView`: name, size, modified, type) and an icons mode (`QListView` in icon mode, with thumbnails). The mode is remembered per window, not per folder, in v1.
- Thumbnails: look up `~/.cache/thumbnails/{normal,large}/<md5 of URI>.png` and check its `Thumb::MTime`. On a miss, generate image thumbnails with `QImageReader` on a thread pool and write them back to the cache following the freedesktop spec, so Dolphin can reuse them. Other types show their MIME icon.
- **Sidebar:** Home, Desktop, Documents, Downloads, Pictures, Music, Videos, Trash, then mounted volumes from `Gio.VolumeMonitor`, with eject. Without GVFS installed the volume monitor only sees mounts, not unmounted drives; that is acceptable for v1.

### Opening files and hand-off

- Double-click or Enter opens with the default app (`Gio.AppInfo.launch_default_for_uri`).
- "Open with" lists `Gio.AppInfo.get_all_for_type(mime)`.
- "Open in Dolphin" (really: the default handler of `inode/directory`, skipping this app) is in the folder's context menu, for anything the browser doesn't do.

### Operations engine (`core/operations/`)

This is the part that has to be right. Every user action that changes files becomes a **Job**.

**Job kinds:** copy, move, trash, restore from trash, permanent delete, create folder, rename. Rename and create folder are synchronous and fast; they don't need an activity, but they go through the same error type.

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
2. **Running.** Execute steps in order. Files copy with `Gio.File.copy(..., progress_callback, cancellable)`, without `OVERWRITE` unless the user chose Replace. Progress is bytes done over bytes planned, so one large file doesn't stall the bar.
3. **Finishing.** The job reports `done` only when every step finished. Anything else is `cancelled` or `failed`.

**Moves.** Call `Gio.File.move` with `G_FILE_COPY_NO_FALLBACK_FOR_MOVE` first, so Gio never does its own copy-and-delete. On the same file system that is an atomic rename. If it fails with `NOT_SUPPORTED` (different file system), the job does a copy of that item and deletes the source only after the copy of that item completed. A cancelled or failed cross-device move therefore never loses data: each source is either still in place or fully at the destination.

**Cancel.** Each job has a `Gio.Cancellable`. Cancelling:

- stops at the current file, deletes the partial destination file of the step in progress (never a file that existed before), and leaves completed files in place;
- reports `cancelled` with a summary: "Cancelled after 5 of 12 files" for copy and move, "Moved 5 of 12 files to the trash" for trash;
- is acknowledged in the UI within a fraction of a second, even mid-file, because `Gio.File.copy` checks the cancellable between chunks.

**Errors.** Every failure is an `OperationError(path, step, message)`, built from the `GLib.Error` with a readable message ("permission denied", "no space left on device", "file name too long"). On an error the job pauses in *waiting for user* and the app asks: Skip, Skip all, Retry, or Cancel. Skipped items are counted, and a job that finishes with skips ends as `done` with "Copied 10 of 12 files, 2 skipped" in its text. **Open decision:** whether a job with skips should end as `done` or `failed`. The rule "never imply it finished when it did not" suggests `failed`, or a `done` whose title states the skips.

**Conflicts.** When a destination exists: Replace, Skip, Keep both (append " (2)" before the extension), each with an "apply to all" option. Replace on a folder merges, it doesn't delete the existing folder. Conflicts are found during planning where possible, so the user answers before the long part starts.

**Trash and delete.** Trash uses `Gio.File.trash`. On a file system without trash support (some removable drives) the job stops and asks whether to delete permanently instead; it never falls back silently. Permanent delete always shows a confirmation with the item count.

**Concurrency.** The queue runs up to two jobs at a time; others wait as `queued`. This also keeps the app well inside the island's limit of 8 activities per sender.

**Undo.** Not in v1. Trash is the safety net. A later "Undo" can cover rename, move (same device) and trash.

### Job model and in-app panel

`JobModel` holds all jobs from this process. Worker threads report through queued signals; the model throttles UI updates to about 10 per second per job.

The **operations panel** is a collapsible strip at the bottom of the window. It shows every job with its title, text, progress and Cancel, keeps finished jobs briefly and failed jobs until dismissed, and opens a details view listing every error. This panel is the primary progress display. The island mirrors it.

**Closing a window with jobs running** asks: "Cancel N operations" or "Keep running". Keep running hides the window and lets the process exit when the queue is empty; the island keeps showing progress meanwhile. If that turns out to be confusing, v1 can simply require cancelling.

### Island integration (`integration/island.py`)

A small client of the activities API. It observes `JobModel` and translates; it holds no job logic.

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
| running | `progress`: bytes done / bytes planned; `text`: current file name and speed |
| waiting for user | progress unchanged, `text`: "Waiting for you in Files" |
| queued | not published until it starts |
| — | `icon`: `edit-copy`, `transform-move`, `user-trash`, `edit-delete` by kind |
| — | `actions`: `[("cancel", "Cancel")]` while running; on failure `[("retry", "Retry"), ("show", "Show")]` |
| done | `state: done`, final title ("Copied 12 files"), empty `text` |
| cancelled | `state: cancelled`, `text`: "Cancelled after 5 of 12 files" |
| failed | `state: failed`, `error`: "report.pdf: permission denied" |

Rules from the API spec that the client must follow:

- **Throttle** progress updates to at most 4 per second per job, and always send the final state immediately. The island's limit is 20 calls per second per sender.
- **Cancel is a request.** On `ActionInvoked(id, "cancel")` cancel the job's `Gio.Cancellable`; the activity stays `running` until the job reports how it ended, and then the client sends that state. Never send `cancelled` before the work has actually stopped.
- **Show for a retry.** A finished activity can't go back to `running` with `Update`. Retry starts a new job, published with `Show` under the old id.
- **`Dismissed(id)`** on a failed job also dismisses it in the in-app panel, so the two stay in sync. Dismissing it in the app calls `Dismiss(id)`.
- **Errors from the island** are logged and never stop a job. `NotFound` means the activity is gone (dismissed or timed out): stop updating it. `LimitExceeded` means too many failed activities are still showing: dismiss the oldest failed one this app owns and try once more. `RateLimited` means drop this update; the next one carries newer values anyway.
- **All D-Bus calls are asynchronous** and made from the UI thread, so a slow or hung island never blocks the app.
- **Crashes need no code.** If the app dies, the island turns its running activities into failures because the connection closed. That is why every job must hold its activity under the app's long-lived connection, not a short-lived one.

### Theme (`integration/theme.py`, `integration/style.py`)

- Port the shell's `examples/read_theme.py`: start from complete defaults, overlay `$XDG_CONFIG_HOME/quickshell-island/theme.json`, ignore unknown fields, fall back per field, and use defaults for a missing or newer `version`.
- `style.py` maps it to Qt:
  - `QPalette`: `background` → Window, `surface` → Base, `surfaceRaised` → AlternateBase and Button, `textPrimary` → WindowText and Text, `textSecondary` → PlaceholderText, `accent` → Highlight, `onAccent` → HighlightedText.
  - Font: the first installed family from `fonts.families`, at `max(8, round(px * scale.font * scale.ui))`.
  - Sizes (row height, icon sizes, paddings) defined in unscaled pixels and multiplied by `scale.ui`.
  - A short stylesheet for corner radii (`radius.small` for buttons and rows, `radius.medium` for panels) and the operations panel. Keep it short: a large stylesheet fights the platform style.
  - `danger` and `success` for failed and finished jobs.
- Read once at startup in v1. Later: watch the **directory** with `QFileSystemWatcher` (the shell replaces the file atomically) and re-apply live.
- Window decorations stay server-side (KWin draws them). Matching the frame to the shell is tracked in shell issue #56.

### Drag and drop and clipboard

- Dragging out sets `text/uri-list`. This is what the dock's future `DropArea` in #32 will accept.
- Dropping in: copy by default across devices, move on the same device, Shift and Ctrl to force move or copy, and a menu when Alt is held, matching Dolphin.
- Cut, copy and paste use `text/uri-list` plus `application/x-kde-cutselection` for cut, so pasting between this app and Dolphin works both ways.

## Implementation plan

Each phase ends with something usable and checked by hand. The core gets tests from phase 2 on.

### Phase 0: skeleton and integration basics

- Project layout, `pyproject.toml`, `.desktop` file, `app.py`.
- Path arguments, `setDesktopFileName`, window title from the folder.
- Theme reader and style, with defaults.
- Spike: Gio D-Bus signal delivered inside `app.exec()`.

**Done when:** `island-files ~/Downloads` opens a themed, empty window titled "Downloads — Files"; the shell's dock shows it as running under its own icon; the launcher's window switcher lists it; the window still opens with `theme.json` deleted.

### Phase 1: browsing

- `listing.py`, `DirectoryModel` with batches and a file monitor, sort and hidden-files proxy.
- Details view, path bar, back/forward/up, sidebar places.
- Open with the default app, Open with, Open in Dolphin.
- Keyboard: Enter, Backspace, Alt+Left/Right/Up, Ctrl+L, Ctrl+H, type-to-select.

**Done when:** browsing a folder of 10 000 files stays responsive; files created elsewhere appear without a refresh.

### Phase 2: operations engine (core, no UI)

- Job, planning, copy, move (same device and cross device), trash, delete, conflicts, errors, queue.
- pytest suite against temporary folders, covering at least:
  - copy and move of nested folders, with byte-based progress;
  - cancel mid-file removes only the partial file and reports how far it got;
  - a cross-device move cancelled or failed halfway leaves every source either in place or complete at the destination (use a tmpfs or a second temporary mount when available, otherwise force the copy-and-delete path);
  - a permission error names the file;
  - copying a folder into itself fails during planning;
  - Keep both picks a free name;
  - `done` is never reported when any step was skipped or failed (whatever the decision above).

**Done when:** the suite passes; no UI yet.

### Phase 3: operations in the UI

- `JobModel`, worker glue, operations panel.
- Copy, cut, paste, drag and drop, delete key (trash), Shift+Delete (confirm, then delete), rename (F2), new folder.
- Conflict and error dialogs; the close-with-running-jobs prompt.

**Done when:** everything in phase 2 can be done from the window, with progress, cancel and errors visible in the panel, and the app works the same with the shell stopped.

### Phase 4: island integration

- `island.py`: version check, name watching, mapping, throttling, actions, dismissal sync, error handling.
- Test the reporter against a fake activities service on a private bus (`dbus-run-session`), or against a fake transport object.
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
- Trash view with restore and empty.
- Properties dialog (size, counts, dates, permissions read-only).
- Remember window size, view mode and hidden-files setting.

### Later

- Live theme updates (file watch on the theme directory).
- Optional custom titlebar when the shell is detected (#21 comment).
- Recent files written to the freedesktop recent-files list, for the shell's launcher (#24, point 4).
- A notification for a job that finished while the window was hidden (#24, point 7).
- Undo for rename, move and trash.
- Remote locations through GVFS, search, archive extraction (each extraction a job, so it reuses the activity mapping).
- Video and PDF thumbnails.

## Open decisions

1. **Widgets or Qt Quick** for the UI (recommendation: Widgets). Decide before phase 1.
2. **A job with skipped items:** `done` with the skips in its title, or `failed`. Decide before phase 2.
3. **Closing with running jobs:** allow "keep running in the background", or require cancelling. Decide in phase 3.
4. **The app's real name and app id.** Decide before the `.desktop` file is installed anywhere it will be pinned in the dock, because the dock stores the desktop file name.
