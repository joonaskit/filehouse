"""What the app remembers between runs: window size, view mode and sort order (the app's,
and each folder's own), hidden files, the split, the tabs of last time, the details pane,
the density, the notifications, whether opened files are remembered, and the rules of
folders."""

import json
import os
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QSize, Signal

from filehouse import APP_ID
from filehouse.core import automations
from filehouse.qt_models import columns

DETAILS, ICONS = "details", "icons"
# What a list can be sorted by, as it is written down
SORTS = columns.KEYS
# A folder whose view was not changed for longest is forgotten past this many
MAX_FOLDERS = 500


class _Changes(QObject):
    # A setting that is shown in every window changed
    remember_opened_changed = Signal(bool)
    details_pane_changed = Signal(bool)
    stay_running_changed = Signal(bool)
    # How folders are shown changed: the app's choice, or the one of some folder
    views_changed = Signal()
    # The columns of the details view changed
    columns_changed = Signal()
    # The rules of some folder changed, or whether rules run at all
    rules_changed = Signal()


changes = _Changes()


def _store():
    # $XDG_CONFIG_HOME/filehouse/filehouse.conf
    return QSettings(APP_ID, APP_ID)


def window_size(default):
    size = _store().value("window/size")
    return size if isinstance(size, QSize) and size.isValid() else default


def set_window_size(size):
    _store().setValue("window/size", size)


def chooser_size(default):
    """The size of the dialog other apps open and save with."""
    size = _store().value("chooser/size")
    return size if isinstance(size, QSize) and size.isValid() else default


def set_chooser_size(size):
    _store().setValue("chooser/size", size)


def chooser_folder():
    """The URI of the folder a file was last chosen in, for an app that names none."""
    return _store().value("chooser/folder", "", type=str)


def set_chooser_folder(uri):
    _store().setValue("chooser/folder", uri)


def view_mode():
    mode = _store().value("view/mode", DETAILS)
    return mode if mode in (DETAILS, ICONS) else DETAILS


def set_view_mode(mode):
    _store().setValue("view/mode", mode)
    changes.views_changed.emit()


def sort():
    """What a list is sorted by, and whether the other way round: (one of SORTS, bool)."""
    key = _store().value("view/sort", "name")
    descending = _store().value("view/sort_descending", False, type=bool)
    return (key, descending) if key in SORTS else ("name", False)


def set_sort(key, descending):
    _store().setValue("view/sort", key)
    _store().setValue("view/sort_descending", bool(descending))
    changes.views_changed.emit()


def stored_columns(kind):
    """The columns that were chosen for the folders of this kind (`columns.FILES` or
    `columns.TRASH`), in order, or None while none were."""
    try:
        stored = json.loads(_store().value(f"view/columns_{kind}", "null"))
    except (TypeError, ValueError):
        return None
    return stored if isinstance(stored, list) else None


def set_columns(kind, keys):
    """The user chose which columns show, and in which order. None goes back to the default."""
    if keys is None:
        _store().remove(f"view/columns_{kind}")
    else:
        _store().setValue(f"view/columns_{kind}", json.dumps(list(keys)))
    changes.columns_changed.emit()


def per_folder():
    """Whether a folder keeps the view and the sort order that were chosen in it."""
    return _store().value("view/per_folder", True, type=bool)


def set_per_folder(on):
    _store().setValue("view/per_folder", bool(on))
    changes.views_changed.emit()


# The folders with a view of their own, by URI: {"mode": …, "sort": …, "descending": …},
# each only when it was chosen there. The one changed last is the last. Kept beside the
# settings in a file of its own, read once
_folders = None


def _folders_path():
    return Path(_store().fileName()).with_name("folders.json")


def _folder_views():
    global _folders
    if _folders is None:
        _folders = {}
        try:
            stored = json.loads(_folders_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = {}
        if isinstance(stored, dict):
            for uri, view in stored.items():
                view = _checked_view(view)
                if view:
                    _folders[uri] = view
    return _folders


def _checked_view(view):
    """What of a stored view can be used: a file that was edited by hand is not trusted."""
    if not isinstance(view, dict):
        return {}
    checked = {}
    if view.get("mode") in (DETAILS, ICONS):
        checked["mode"] = view["mode"]
    if view.get("sort") in SORTS:
        checked["sort"] = view["sort"]
        checked["descending"] = bool(view.get("descending", False))
    return checked


def _write_folder_views():
    path = _folders_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Under another name and moved into place: never half a file
        temporary = path.with_name(path.name + ".new")
        temporary.write_text(json.dumps(_folders, indent=1), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        # Not remembered then: the view still changed
        pass


def folder_view(uri):
    """What was chosen in this folder itself: a dict that may be empty."""
    return dict(_folder_views().get(uri, {}))


def view_for(uri):
    """How a folder is shown: (mode, one of SORTS, descending). Its own choices where
    folders keep theirs, and the app's for the rest."""
    own = folder_view(uri) if per_folder() else {}
    key, descending = sort()
    if "sort" in own:
        key, descending = own["sort"], own["descending"]
    return own.get("mode", view_mode()), key, descending


def choose_view(uri, mode=None, sort=None):
    """The user changed the view or the sort order, `(one of SORTS, descending)`, in a
    folder: it is that folder's where folders keep theirs, and everyone's where not."""
    if not per_folder():
        if mode is not None:
            _store().setValue("view/mode", mode)
        if sort is not None:
            _store().setValue("view/sort", sort[0])
            _store().setValue("view/sort_descending", bool(sort[1]))
        changes.views_changed.emit()
        return
    views = _folder_views()
    view = views.pop(uri, {})
    if mode is not None:
        view["mode"] = mode
    if sort is not None:
        view["sort"], view["descending"] = sort[0], bool(sort[1])
    views[uri] = view
    while len(views) > MAX_FOLDERS:
        del views[next(iter(views))]
    _write_folder_views()
    changes.views_changed.emit()


def forget_folder_view(uri):
    """Back to the app's view in this folder."""
    if _folder_views().pop(uri, None) is not None:
        _write_folder_views()
        changes.views_changed.emit()


def show_hidden():
    return _store().value("view/show_hidden", False, type=bool)


def set_show_hidden(show):
    _store().setValue("view/show_hidden", bool(show))


def density():
    """How much room the lists and the sidebar take; read at the start."""
    value = _store().value("view/density", "comfortable")
    return value if value in ("comfortable", "compact") else "comfortable"


def set_density(density):
    _store().setValue("view/density", density)


def notify():
    """Whether a job that ends with no window showing is told in a notification."""
    return _store().value("notifications/enabled", True, type=bool)


def set_notify(notify):
    _store().setValue("notifications/enabled", bool(notify))


def long_job_seconds():
    """With the island showing jobs, how long a job runs before it is worth a notification."""
    return max(1, _store().value("notifications/long_job_seconds", 10, type=int))


def set_long_job_seconds(seconds):
    _store().setValue("notifications/long_job_seconds", max(1, int(seconds)))


def details_pane():
    """Whether a wide window shows the pane of details beside the list."""
    return _store().value("view/details_pane", True, type=bool)


def set_details_pane(show):
    _store().setValue("view/details_pane", bool(show))
    changes.details_pane_changed.emit(bool(show))


def split():
    return _store().value("view/split", False, type=bool)


def set_split(split):
    _store().setValue("view/split", bool(split))


def restore_tabs():
    """Whether a start without a path opens the tabs the last window had."""
    return _store().value("window/restore_tabs", True, type=bool)


def set_restore_tabs(on):
    _store().setValue("window/restore_tabs", bool(on))


def stay_running():
    """Whether the process goes on without a window when the last one is closed, so
    the next one is there sooner: it is opened by a process that is running already."""
    return _store().value("window/stay_running", True, type=bool)


def set_stay_running(on):
    _store().setValue("window/stay_running", bool(on))
    changes.stay_running_changed.emit(bool(on))


# The rules of folders (#87), by URI: a list of rules each, in order. Kept beside the
# settings in a file of their own, as the views of folders are, and read once
_rules = None


def _rules_path():
    return Path(_store().fileName()).with_name("rules.json")


def _stored_rules():
    global _rules
    if _rules is None:
        _rules = {}
        try:
            stored = json.loads(_rules_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = {}
        if isinstance(stored, dict):
            for uri, items in stored.items():
                rules = automations.rules_from_json(items)
                if rules:
                    _rules[str(uri)] = rules
    return _rules


def folder_rules(uri):
    """The rules of the folder at `uri`, in order: a list that may be empty."""
    return list(_stored_rules().get(uri, ()))


def all_folder_rules():
    """{uri: rules} of every folder with rules."""
    return {uri: list(rules) for uri, rules in _stored_rules().items()}


def set_folder_rules(uri, rules):
    stored = _stored_rules()
    if rules:
        stored[uri] = list(rules)
    else:
        stored.pop(uri, None)
    path = _rules_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Under another name and moved into place: never half a file
        temporary = path.with_name(path.name + ".new")
        data = {uri: automations.rules_to_json(rules) for uri, rules in stored.items()}
        temporary.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        # Kept for as long as the process runs, then
        pass
    changes.rules_changed.emit()


def run_rules():
    """Whether the rules of folders run at all: one switch that pauses every rule."""
    return _store().value("rules/run", True, type=bool)


def set_run_rules(on):
    _store().setValue("rules/run", bool(on))
    changes.rules_changed.emit()


def rules_last_run():
    """When the rules last ran by themselves, by the clock; 0 for never."""
    return _store().value("rules/last_run", 0.0, type=float)


def set_rules_last_run(when):
    _store().setValue("rules/last_run", float(when))


def session():
    """The tabs of the window that was closed last: a list of (URIs of its one or two
    folders, which of them was the active one) and the tab that was in front. None when
    there is nothing to open."""
    try:
        stored = json.loads(_store().value("window/tabs", "", type=str))
        tabs = [
            ([str(uri) for uri in tab["folders"]][:2], int(tab["active"]))
            for tab in stored["tabs"]
            if tab["folders"]
        ]
        front = int(stored["front"])
    except (ValueError, KeyError, TypeError):
        return None
    return (tabs, front) if tabs else None


def set_session(tabs, front):
    stored = {
        "tabs": [{"folders": list(folders), "active": active} for folders, active in tabs],
        "front": front,
    }
    _store().setValue("window/tabs", json.dumps(stored))


def remember_opened():
    """Whether files opened from the app are told to the desktop's lists of recent files."""
    return _store().value("privacy/remember_opened", True, type=bool)


def set_remember_opened(remember):
    _store().setValue("privacy/remember_opened", bool(remember))
    changes.remember_opened_changed.emit(bool(remember))


def collapsed_groups():
    """The groups of the sidebar that are folded shut, by key."""
    value = _store().value("sidebar/collapsed", [])
    if isinstance(value, str):
        value = [value]
    return [str(key) for key in value or []]


def set_group_collapsed(key, collapsed):
    groups = [group for group in collapsed_groups() if group != key]
    if collapsed:
        groups.append(key)
    _store().setValue("sidebar/collapsed", groups)


def clear():
    global _folders, _rules
    _store().clear()
    _folders = None
    _rules = None
    for path in (_folders_path(), _rules_path()):
        try:
            path.unlink()
        except OSError:
            pass
