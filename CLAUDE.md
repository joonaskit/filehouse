# CLAUDE.md

A small file browser for KDE Plasma (Wayland), companion to the Quickshell Island shell but usable without it. [ARCHITECTURE.md](ARCHITECTURE.md) is the source of truth for design, phases and open decisions; read the relevant section before implementing anything, and update it when a decision changes.

## Stack

- Python 3.12+, **PySide6 with Qt Widgets** (not Qt Quick/QML)
- **Gio via PyGObject** for all file system work, trash, MIME, default apps and file monitors
- **Gio.DBusConnection** for D-Bus (not QtDBus)
- pytest for `core/`, pytest-qt for the few UI tests

## Setup and commands

```sh
sudo dnf install python3-pyside6 python3-gobject
python3 -m venv --system-site-packages .venv   # system site packages are needed for gi
source .venv/bin/activate
pip install -e '.[dev]'

filehouse ~/Downloads       # run the app
ruff check . && ruff format --check .

pytest                     # all tests
pytest tests/core          # operations engine, no display needed
pytest tests/core/test_copy.py::test_name
pytest tests/ui            # runs offscreen (set in tests/conftest.py)
```

Do not `pip install PySide6` into the venv: mixing a PyPI Qt with the system PyGObject risks losing the shared GLib event loop the design depends on.

## Naming

The app, package, command and app id are all `filehouse` (`APP_ID` in `src/filehouse/__init__.py`). The `.desktop` file name, `StartupWMClass`, and `QGuiApplication.setDesktopFileName()` must be that same string.

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
- `JobModel` is the single source of truth for job state. The in-app operations panel and the island reporter both observe it; the island client holds no job logic.

## Safety rules for file operations

These are requirements, not guidelines. Any change in `core/operations/` needs a test that covers them.

1. A job reports `done` only when every step finished. Progress `1.0` is not done.
2. A move never deletes a source before its copy at the destination is complete. Use `Gio.File.move` with `NO_FALLBACK_FOR_MOVE`, then copy-and-delete per item on `NOT_SUPPORTED`.
3. Cancel removes only the partial destination file of the step in progress, never a file that existed before, and reports how far it got ("Cancelled after 5 of 12 files").
4. Every failure is an `OperationError(path, step, message)` naming the file.
5. Delete goes to the trash. Permanent delete always asks first, and trash never falls back to permanent delete silently.
6. Never pass `OVERWRITE` unless the user chose Replace. Planning-time conflict checks are advisory; handle `EXISTS` at run time as well.

## Conventions

- Gio objects that deliver signals (`FileMonitor`, `VolumeMonitor`, D-Bus subscriptions) are created on the main thread so Qt's GLib dispatcher delivers them. Blocking Gio calls (`enumerate_children`, `copy`) run on worker threads.
- All D-Bus calls are asynchronous. A slow or absent island must never block the UI or stop a job.
- Island updates: at most 4 per second per job, final state sent immediately, never `cancelled` before the work has actually stopped.
- Keep the stylesheet short; colours and fonts go through `QPalette` and `QFont` from the theme.
- Sizes are defined in unscaled pixels and multiplied by the theme's `scale.ui`.
- Tests run against `tmp_path`; never touch the real home directory or the real trash. `tests/conftest.py` points `HOME` and the XDG directories at a temporary folder before `gi` loads; tests that need a working trash use the `home_dir` fixture.
