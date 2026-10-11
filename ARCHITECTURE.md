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
| Thumbnails | Freedesktop thumbnail cache, plus `QImageReader` for images | Shares the cache with Dolphin. Video and PDF thumbnails come later |
| Tests | pytest (core), pytest-qt (a few UI tests) | The core has no UI and can be tested against temporary folders |

On Fedora: `dnf install python3-pyside6 python3-gobject`. For development, a venv created with `--system-site-packages` sees both.

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
- **One source of truth for job state:** `JobModel`. Both the in-app operations panel and the island reporter observe it. The island is a second view of it, never the only one, because the app must work without the shell.

### Project layout

```
filehouse/
├── ARCHITECTURE.md              this document
├── README.md
├── pyproject.toml               entry point: filehouse = filehouse.app:main
├── data/
│   ├── filehouse.desktop     Exec=filehouse %U, StartupWMClass=filehouse
│   └── icons/
├── src/filehouse/
│   ├── app.py                   argument parsing, QApplication, desktop file name, first window
│   ├── core/
│   │   ├── locations.py         path/URI handling, places (home, Downloads, …), display names
│   │   ├── listing.py           batched directory enumeration, file info records
│   │   ├── history.py           back and forward history for one window
│   │   ├── properties.py        details of a file, size of a folder all the way down
│   │   ├── errors.py            OperationError: which file, which step, readable message
│   │   └── operations/
│   │       ├── job.py           Job, JobState, JobProgress
│   │       ├── plan.py          expand a request into concrete steps, count bytes
│   │       ├── copy.py          copy and move (same-device rename, cross-device copy+delete)
│   │       ├── trash.py         trash (restore is added in phase 5)
│   │       ├── delete.py        permanent delete
│   │       ├── conflicts.py     the questions a job asks, answers, names for "Keep both"
│   │       ├── quick.py         new folder and rename: immediate, no job
│   │       └── queue.py         runs jobs with a concurrency limit
│   ├── qt_models/
│   │   ├── directory_model.py   QAbstractItemModel over listing + Gio.FileMonitor
│   │   ├── icons.py             icon tile per kind of file
│   │   ├── dates.py             "Today, 10:42", "Yesterday", a weekday, a date
│   │   ├── thumbnails.py        freedesktop thumbnail cache: load, make, write back
│   │   ├── volumes.py           drives and mounts from Gio.VolumeMonitor, mount and eject
│   │   ├── measure.py           folder sizes on a worker thread
│   │   ├── details.py           one file's details on a worker thread
│   │   ├── job_model.py         all jobs of the process, fed by worker threads; titles and texts
│   │   └── workers.py           the thread pool jobs run on
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
│       ├── clipboard.py         cut, copy and paste of files as `text/uri-list`
│       ├── settings.py          what is remembered between runs
│       └── dialogs/             questions (conflict, error, no trash, confirm delete) and the
│                                prompter that shows them, name (rename, new folder), details,
│                                properties
└── tests/
    ├── core/                    copy, move, trash, cancel, failure, conflicts against tmp dirs
    └── integration/             island reporter against a fake bus, theme reader
```

## Components

### Startup (`app.py`)

1. Parse arguments: zero or more paths or `file://` URIs (`%U` from the `.desktop` file). A file argument opens its folder with the file selected. A missing path opens home and shows a message.
2. `QGuiApplication.setDesktopFileName("filehouse")`. On Wayland this sets the app id, which the shell's `DockService.findToplevels` compares **exactly** against `StartupWMClass`. This is what gives point 3a for free.
3. Load the theme and apply the style before creating any window.
4. Open one window per path argument (one window when there are none).

Every launch is a new process. There is no single-instance logic and no D-Bus name in v1 (#24, point 6).

### Window title

`"<folder name> — Files"`, updated on every navigation. Home shows as `Home`, root as `/`. The KWin script feeds this to the shell's window switcher (#24, point 5).

### Browsing

- **`core/listing.py`** enumerates a folder with `Gio.File.enumerate_children` on a worker thread, in batches of about 200, asking only for the attributes the view needs (`standard::*`, `time::modified`, `unix::mode`, `thumbnail::*`). Large folders appear progressively.
- **`DirectoryModel`** holds one folder. It appends batches, then follows changes with a `Gio.FileMonitor` (created, deleted, renamed, changed). Sorting and the hidden-files filter are a `QSortFilterProxyModel` on top: folders first, then natural order by name, case-insensitive.
- Navigation keeps a back/forward history per window. Up goes to the parent.
- **`FolderView`** has a details mode (`QTreeView`: name, permissions, modified, size) and an icons mode (`QListView` in icon mode, with thumbnails). The mode is remembered per window, not per folder, in v1.
- Thumbnails: look up `~/.cache/thumbnails/{normal,large}/<md5 of URI>.png` and check its `Thumb::MTime`. On a miss, generate image thumbnails with `QImageReader` on a thread pool and write them back to the cache following the freedesktop spec, so Dolphin can reuse them. Other types show their MIME icon.
- **Sidebar:** Home, Desktop, Documents, Downloads, Pictures, Music, Videos, Trash, then mounted volumes from `Gio.VolumeMonitor`, with eject. Without GVFS installed the volume monitor only sees mounts, not unmounted drives; that is acceptable for v1.

### Opening files and hand-off

- Double-click or Enter opens with the default app (`Gio.AppInfo.launch_default_for_uri`).
- "Open with" lists `Gio.AppInfo.get_all_for_type(mime)`.
- "Open in Dolphin" (really: the default handler of `inode/directory`, skipping this app) is in the folder's context menu, for anything the browser doesn't do.

### Operations engine (`core/operations/`)

This is the part that has to be right. Every user action that changes files becomes a **Job**.

**Job kinds:** copy, move, trash, permanent delete, create folder, rename. Restore from trash is a job kind too, but it is built in phase 5 together with the trash view, so it is designed against the view that triggers it. Rename and create folder are synchronous and fast; they don't need an activity, but they go through the same error type.

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
2. **Running.** Execute steps in order. Files copy with `Gio.File.copy(..., progress_callback, cancellable)` to a temporary name next to the destination (`.filehouse-<random>.part`) and are renamed into place once complete, without `OVERWRITE` unless the user chose Replace. A half-written file therefore never appears under the real name, a file being replaced stays whole until its replacement is, and the temporary file is always the job's own to remove. Copies keep modification times and permissions (`ALL_METADATA`). Progress is bytes done over bytes planned, so one large file doesn't stall the bar.
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

**Concurrency.** The queue runs up to two jobs at a time; others wait as `queued`. The core starts no threads: `JobQueue` is given a `spawn` function by `qt_models/`. A job cancelled while it is still queued reports `cancelled` at once and is never started. This also keeps the app well inside the island's limit of 8 activities per sender.

**Undo.** Not in v1. Trash is the safety net. A later "Undo" can cover rename, move (same device) and trash.

### Job model and in-app panel

`JobModel` holds all jobs from this process: one instance, made in `app.py` and given to every window. It is a `QObject` with signals (`job_added`, `job_changed`, `job_removed`, `question_asked`, `idle`), not an item model, because its observers are a panel of widgets and the island reporter. Worker threads report through queued signals; the model passes on every state change and otherwise at most about 10 updates per second per job. It also decides how long an ended job stays listed: finished and cancelled jobs for a few seconds, failed jobs until dismissed. Retry replaces the failed job with a new one for the failed items.

The **operations panel** is a collapsible strip at the bottom of the window. It shows every job with its title, text, progress and Cancel, and for a failed job Retry, Details (every error) and Dismiss. Each window has a panel and they all show the same jobs. This panel is the primary progress display. The island mirrors it.

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

**Status:** phases 0 and 1 are implemented as v0.1 alpha. Phases 2, 3 and 4 are implemented and tested, and phase 4 was checked by hand with the shell running. Phase 5 is implemented and tested; mounting and ejecting were only tested against stand-ins for drives. Phase 6, the new design, is under way: steps 6a, 6b and 6c are done.

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
- **Remembered** in `$XDG_CONFIG_HOME/filehouse/filehouse.conf`: window size (saved when a window closes), view mode and whether hidden files are shown.

### Phase 6: the new design

The design is six pictures in `desing/` (not in git): a wide window with three panes, and a narrow window with its menus and sheets. This phase brings the app to that design. It is split into steps that each leave the app usable, ordered so that restyling what exists comes first and new behaviour after.

The custom title bar in the pictures is **not** part of this phase. It is a decision of its own, in issue #9; every step below works under KWin's normal title bar.

**Theme in this phase.** Everything the design needs is already in the shell's `theme.json` (`docs/THEME.md` in the shell repo), so nothing has to be added to the theme:

- **Icon tiles** take their colours from `colors.palette` (green, blue, orange, yellow, red, purple, cyan, indigo), which the theme provides "for categories and decoration". The app decides which place and which kind of file gets which palette colour; the colour values are the shell's. A tile is the palette colour at low opacity with the icon in the full colour.
- **Selection, focus and the progress bar** use `colors.accent`, which the user picks in the shell's settings, with `colors.onAccent` on top of it. Status colours come from `success`, `warning` and `danger`, never from the palette.
- **Corners** use `radius.small`, `medium` and `large`. These follow the user's corner roundness setting, so the window only looks as round as the pictures when the shell is set to "Round". The app does not hard-code the pictures' radii.
- **Light scheme.** The shell has an experimental light mode and exports `scheme` with matching colours. Nothing in the app may assume a dark background: translucent overlays (hover, dividers) are derived from the text colour, not from white.
- **Reader.** `integration/theme.py` currently defaults `palette` to empty and keeps whatever entries the file has without checking them. It gets the full default palette and the same colour check as the other colours, so the tiles work with no theme file.

**6a. Restyle what exists.** No new behaviour.

- Toolbar as rounded groups: back/forward/up, the breadcrumb, the action icons (copy, move, rename, trash), the list/grid switch.
- Sidebar as a rounded card: a coloured icon tile per place, and a "Devices" heading above the drives.
- List rows: taller, with an icon tile per file and a rounded highlight for the selection.
- Menus: an icon per entry, shortcuts on the right, "Move to trash" in the danger colour.
- Status bar text: "11 items · Dock.qml selected".

**Done when:** the wide window matches the picture apart from the title bar, the details pane and the columns.

**Done.** How it was built:

- `integration/style.py` has the helpers the rest of the phase uses: `palette_color`, `radius`, `overlay` (the text colour at an opacity), `icon` (a theme icon in one colour) and `tile` (an icon on a rounded tile in a palette colour). Icons are drawn once as ordinary pixmaps; a custom icon engine was tried and dropped, because Qt can outlive Python at exit and then calls into an engine that is gone. `style.generation` goes up with each applied theme, so caches of icons know when to start over.
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
- Not done: the sort order is not remembered between runs; a new window sorts by name.

**6c. Details pane.**

- A pane on the right for the selection: icon, name, type, modified, size, owner, the permissions grid (read, write, run for owner, group, others) and an Open button.
- With nothing selected it shows the open folder; with several items, their count and total size.
- It reuses `core/properties.py` and the folder measuring from phase 5. The Properties dialog stays for now and is removed once the pane covers everything it shows.

**Done when:** selecting a file shows its details without opening a dialog.

**Done.** How it was built:

- `ui/details_pane.py` is the third part of the window's splitter, a card like the sidebar. It is given the open folder and the selected entries and decides by itself what to show. Asked again for what it already shows, it does nothing; an entry whose size, date or mode changed counts as new, so a selected file that changes is read again (the window looks for that at most five times a second).
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

**6e. Narrow window.**

- Below a width to be chosen, the window changes layout: the sidebar becomes an icon rail, the breadcrumb shortens to "… › parent › folder", the action icons move to a floating bar at the bottom, and the details pane becomes a sheet that slides up.
- A "⋯" button opens the folder menu.

**Done when:** resizing the window across the width switches between the two layouts without losing the selection or the place in the list.

**6f. Small additions from the menus.**

- Copy path.
- Open terminal here (the default terminal; hidden when none is found).
- New file.
- A usage bar per drive in the sidebar.
- "Move" in the toolbar: move the selection to a folder that is picked.

**6g. Changing permissions.**

- The four presets from the pictures, from the Permissions submenu and the lock button: Private 600, Shared 664, Make executable 755, Read-only 444.
- It changes files, so it goes through the core with the same error type and tests as every other operation.

**Left for later:** search (the field in the toolbar) and Compress. Both are on the "Later" list already, and both are features of their own.

**Open questions for this phase**

1. ~~Colours of the icon tiles~~ Decided: from the theme's `colors.palette`, see "Theme in this phase". Which colour goes with which place and file type is fixed in the app.
2. **Folder presets.** 600 or 444 on a folder makes it impossible to open. Do folders get their own presets (700, 775, 755, 555), and is a preset applied to what is inside a folder as well?
3. **The permissions grid.** Is it only a display, or are the dots switches?
4. **The width at which the layout changes**, and whether the user can force one layout.
5. **Properties dialog.** Remove it when the details pane is done, or keep it for several items?
6. **New file.** An empty file with a typed name, or a list of templates as the submenu arrow suggests?

### Later

- Live theme updates (file watch on the theme directory).
- Custom title bar: a decision of its own, see issue #9 (and #21 comment in the shell repo).
- Recent files written to the freedesktop recent-files list, for the shell's launcher (#24, point 4).
- A notification for a job that finished while the window was hidden (#24, point 7).
- Undo for rename, move and trash.
- Remote locations through GVFS, search, archive extraction (each extraction a job, so it reuses the activity mapping).
- Video and PDF thumbnails.

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
