# CLAUDE.md

A small file browser for KDE Plasma (Wayland), companion to the Quickshell Island shell but usable without it. [ARCHITECTURE.md](ARCHITECTURE.md) is the source of truth for design, phases and open decisions; read the relevant section before implementing anything, and update it when a decision changes.

## Stack

- Python 3.12+, **PySide6 with Qt Widgets** (not Qt Quick/QML)
- **Gio via PyGObject** for all file system work, trash, MIME, default apps and file monitors
- **Gio.DBusConnection** for D-Bus (not QtDBus)
- **libarchive via `libarchive-c`** for archives, and it is optional: without it the app runs and offers nothing for them
- pytest for `core/`, pytest-qt for the few UI tests

## Setup and commands

```sh
sudo dnf install python3-pyside6 python3-gobject python3-libarchive-c
python3 -m venv --system-site-packages .venv   # system site packages are needed for gi
source .venv/bin/activate
pip install -e '.[dev]'

filehouse ~/Downloads       # run the app
filehouse --quit            # end the process that stays without a window: it runs the old code until then
ruff check . && ruff format --check .

pytest                     # all tests
pytest tests/core          # operations engine, no display needed
pytest tests/core/test_copy.py::test_name
pytest tests/ui            # runs offscreen (set in tests/conftest.py)
```

Every push runs `ruff` and `pytest` on GitHub (`.github/workflows/tests.yml`), in a Fedora container and as an ordinary user: as root the tests of what happens when a file cannot be read or written are skipped. It installs `python3-libarchive-c`, so the archive tests run there even where they are skipped locally.

Do not `pip install PySide6` into the venv: mixing a PyPI Qt with the system PyGObject risks losing the shared GLib event loop the design depends on.

## Naming

The app, package, command and app id are all `filehouse` (`APP_ID` in `src/filehouse/__init__.py`). The `.desktop` file name, `StartupWMClass`, and `QGuiApplication.setDesktopFileName()` must be that same string. The one name that is not is the D-Bus name the process owns, `io.github.joonaskit.filehouse` (`integration/instance.py`): a bus name needs dots. As the file chooser portal's backend it also owns `org.freedesktop.impl.portal.desktop.filehouse` (`integration/portal.py`), the name `data/filehouse.portal` gives the portal.

## Layer rules

```
ui/  →  qt_models/  →  core/  →  Gio
          integration/ (theme, island D-Bus client, desktop hand-off)
```

- `core/` imports **no Qt**. Gio and plain Python only; it reports through callbacks.
- `qt_models/` is the only place that runs core work on threads and turns callbacks into queued Qt signals.
- `ui/` never calls Gio or D-Bus directly. It passes locations (`Gio.File`) around as opaque values and asks `core/locations.py` for anything it needs from them.
- `tests/test_layers.py` enforces these import rules.
- `integration/` must degrade silently: no island means no activities, no `theme.json` means built-in defaults. The app must work with the shell stopped.
- A call from the file chooser portal is always answered (`integration/portal.py`): another app's Open dialog waits for it. Chosen, cancelled or failed, never left open.
- `JobModel` is the single source of truth for job state. The in-app operations panel and the island reporter both observe it; the island client holds no job logic.

## Safety rules for file operations

These are requirements, not guidelines. Any change in `core/operations/` needs a test that covers them.

1. A job reports `done` only when every step finished. Progress `1.0` is not done.
2. A move never deletes a source before its copy at the destination is complete. Use `Gio.File.move` with `NO_FALLBACK_FOR_MOVE`, then copy-and-delete per item on `NOT_SUPPORTED`.
3. Cancel removes only the partial destination file of the step in progress, never a file that existed before, and reports how far it got ("Cancelled after 5 of 12 files").
4. Every failure is an `OperationError(path, step, message)` naming the file.
5. Delete goes to the trash. Permanent delete always asks first, and trash never falls back to permanent delete silently.
6. Never pass `OVERWRITE` unless the user chose Replace. Planning-time conflict checks are advisory; handle `EXISTS` at run time as well.
7. Nothing in an archive is trusted. A path from one is used only when `archives.safe_parts` accepts it, nothing is written through a link, and an extraction or a new archive never replaces what exists.

## Conventions

- Gio objects that deliver signals (`FileMonitor`, `VolumeMonitor`, D-Bus subscriptions) are created on the main thread so Qt's GLib dispatcher delivers them. Blocking Gio calls (`enumerate_children`, `copy`) run on worker threads.
- A worker answers through `workers.Outbox`, never by emitting a signal of an object its owner can delete: a window closing mid-emit crashes the process (#94).
- All D-Bus calls are asynchronous. A slow or absent island must never block the UI or stop a job.
- Island updates: at most 4 per second per job, final state sent immediately, never `cancelled` before the work has actually stopped.
- Colours and fonts go through `QPalette` and `QFont` from the theme. The stylesheet is for shapes (rounded groups, cards, rows, menus) and takes every colour, radius and size from `integration/style.py`: no literal colours, and nothing that assumes a dark background. Icons come from `style.icon` and `style.tile`.
- Sizes are defined in unscaled pixels and multiplied by the theme's `scale.ui`.
- Durations and easings come from `integration/motion.py`, and a duration is asked for with `motion.ms()` when the animation starts: the desktop's animation speed scales it, and at zero nothing is animated.
- Tests run against `tmp_path`; never touch the real home directory or the real trash. `tests/conftest.py` points `HOME` and the XDG directories at a temporary folder before `gi` loads; tests that need a working trash use the `home_dir` fixture.
