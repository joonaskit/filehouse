"""One folder in a window: its model, sort order, list, history and selection."""

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from filehouse.core import locations
from filehouse.core.history import History
from filehouse.qt_models import columns
from filehouse.qt_models.directory_model import DirectoryModel, DirectorySortProxy
from filehouse.ui import settings
from filehouse.ui.folder_view import FolderView

# The folders below are searched this long after the last key: the list is filtered at once
_SEARCH_DELAY_MS = 250
# The columns a list is sorted by, as the settings name them
_SORTS = dict(enumerate(columns.KEYS))


class Pane(QObject):
    # Anything the window's actions, status bar and details depend on has changed
    changed = Signal()
    # A different folder is shown, or the same one again after a reload
    location_changed = Signal(object)

    def __init__(self, show_hidden, parent=None):
        super().__init__(parent)
        self.history = History()
        self.location = None
        self.error = None
        self._pending_select = None
        self._pending_names = set()
        # While the view is given the sort order of a folder: that is not a choice
        self._applying = False
        self._closed = False
        self.search_text = ""
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(_SEARCH_DELAY_MS)
        self._search_timer.timeout.connect(self._search_below)

        self.model = DirectoryModel(self)
        self.proxy = DirectorySortProxy(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.set_show_hidden(show_hidden)
        self.model.loaded.connect(self._on_loaded)
        self.model.load_failed.connect(self._on_load_failed)
        self.model.search_changed.connect(self.changed)
        self.view = FolderView(self.proxy)
        self.view.selection_model().selectionChanged.connect(self.changed)
        self.view.sort_changed.connect(self._on_sort_chosen)
        self.view.columns_chosen.connect(self._on_columns_chosen)
        self.model.search_changed.connect(self._use_columns)
        settings.changes.views_changed.connect(self._use_view)
        settings.changes.columns_changed.connect(self._use_columns)

        for signal in (
            self.proxy.rowsInserted,
            self.proxy.rowsRemoved,
            # The proxy drops a row before the model has: without this, a folder whose
            # last file goes would be told it only has hidden files
            self.model.rowsRemoved,
            self.proxy.modelReset,
            self.proxy.layoutChanged,
        ):
            signal.connect(self.changed)

    # Navigation

    def navigate(self, location, select=None):
        """Go to a folder and record it in the history."""
        if locations.same(location, self.location):
            return
        self.history.visit(location)
        self.show(location, select)

    def go_back(self):
        if self.history.can_go_back:
            previous = self.location
            self.show(self.history.back(), self._name_within(previous, self.history.current))

    def go_forward(self):
        if self.history.can_go_forward:
            self.show(self.history.forward())

    def go_up(self):
        parent = locations.parent(self.location)
        if parent is not None:
            # Arrive with the folder we came from selected
            self.navigate(parent, select=locations.basename(self.location))

    def reload(self):
        """List the folder again. A search that is on stays on."""
        text = self.search_text
        self.show(self.location)
        self.set_search(text)

    def show(self, location, select=None):
        # A search is of the folder it was typed in: it ends with it
        self._search_timer.stop()
        self.search_text = ""
        self.proxy.set_filter_text("")
        self.location = location
        self._use_view()
        self._pending_select = select
        self._pending_names = set()
        self.error = None
        self.model.set_location(location)
        self._use_columns()
        self.location_changed.emit(location)

    # How the folder is shown

    def _use_view(self):
        """Show the folder as it was chosen for it, or as the app's choice says. The
        recent files are always the latest first, whatever was chosen."""
        if self.location is None:
            return
        mode, key, descending = settings.view_for(locations.uri(self.location))
        if self.in_recent:
            key, descending = "modified", True
        if key not in columns.offered(self._columns_kind):
            # A column of the trash, chosen there, in a folder that has no such column
            key = "name"
        order = Qt.SortOrder.DescendingOrder if descending else Qt.SortOrder.AscendingOrder
        changed = mode != self.view.mode
        self._applying = True
        try:
            self.view.set_mode(mode)
            self.view.sort(columns.column(key), order)
        finally:
            self._applying = False
        if changed:
            self.changed.emit()

    @property
    def _columns_kind(self):
        return columns.TRASH if self.in_trash else columns.FILES

    def _use_columns(self):
        """Show the columns that were chosen for this kind of folder."""
        if self.location is None:
            return
        kind = self._columns_kind
        keys = columns.choose(settings.stored_columns(kind), kind)
        if self.model.search_text and "permissions" not in keys:
            # The results come from many folders, and the column says which
            keys.insert(1, "permissions")
        self.view.set_columns(columns.offered(kind), keys)

    def _on_columns_chosen(self, keys):
        kind = self._columns_kind
        if not keys:
            settings.set_columns(kind, None)
            return
        chosen = columns.choose(settings.stored_columns(kind), kind)
        if self.model.search_text and "permissions" not in chosen:
            # Put there by the search, not chosen
            keys = [key for key in keys if key != "permissions"]
        settings.set_columns(kind, keys)

    def choose_mode(self, mode):
        """The user wants this folder as details or as icons."""
        settings.choose_view(locations.uri(self.location), mode=mode)

    def _on_sort_chosen(self, column, order):
        # In the recent files a sort is for now: they are the latest first when entered
        if self._applying or self.location is None or self.in_recent:
            return
        descending = order == Qt.SortOrder.DescendingOrder
        settings.choose_view(locations.uri(self.location), sort=(_SORTS[column], descending))

    @property
    def has_own_view(self):
        """Whether this folder is shown its own way, which "Use the default here" ends."""
        return settings.per_folder() and bool(settings.folder_view(locations.uri(self.location)))

    def use_default_view(self):
        settings.forget_folder_view(locations.uri(self.location))

    @staticmethod
    def _name_within(location, folder):
        """The name of `location` if it sits directly in `folder`, to select it there."""
        if locations.same(locations.parent(location), folder):
            return locations.basename(location)
        return None

    # Searching

    def set_search(self, text):
        """Show what matches `text`: of this folder at once, and of the folders below
        it as it is found. An empty text ends the search."""
        if text == self.search_text:
            return
        self.search_text = text
        self.proxy.set_filter_text(text)
        if text.strip():
            self._search_timer.start()
        else:
            self._search_timer.stop()
            self.model.set_search("")
        self.changed.emit()

    def search_again(self):
        """The files below may have changed (a job ended): nothing watches them."""
        if self.model.search_text:
            self._search_below()

    def _search_below(self):
        self.model.set_search(self.search_text, self.proxy.show_hidden)

    @property
    def searching(self):
        """Whether results may still come."""
        return bool(self.search_text.strip()) and (
            self._search_timer.isActive() or self.model.searching
        )

    # State

    def select_when_listed(self, folder, name):
        """Select an entry that something made or renamed, once it is listed."""
        if locations.same(folder, self.location):
            self._pending_select = name
            self.changed.emit()

    def select_names_when_listed(self, folder, names):
        """Select several entries that were renamed together, once all are listed."""
        if locations.same(folder, self.location):
            self._pending_names = set(names)
            self.changed.emit()

    def _on_loaded(self):
        self.select_pending()
        self.changed.emit()

    def select_pending(self):
        name = self._pending_select
        # The model may have a row a moment before the list has: then it stays pending
        if name and self.model.row_for_name(name) >= 0 and self.view.select_name(name):
            self._pending_select = None
        names = self._pending_names
        if names and all(self.model.row_for_name(name) >= 0 for name in names):
            self._pending_names = set()
            # Once the list has caught up with the model. Those it shows: a filter or
            # the hidden files may leave some out
            QTimer.singleShot(0, self, lambda: self._select_names(names))

    def _select_names(self, names):
        if not self._closed:
            self.view.select_where(lambda info: info.name in names)

    def shown_infos(self):
        """Every entry the list shows, selected or not."""
        return [self.view.info_at(row) for row in range(self.proxy.rowCount())]

    def _on_load_failed(self, message):
        self.error = message
        self.changed.emit()

    def sync(self):
        """Bring the list in line with the model: a pending selection, and what it
        says when there is nothing to list."""
        # Something this window made (a new folder, a renamed file) is selected once it is listed
        if not self.model.loading:
            self.select_pending()
        count = self.proxy.rowCount()
        if self.error:
            self.view.show_message(self.error)
        elif count or self.model.loading:
            self.view.show_items()
        elif self.search_text.strip():
            if self.searching:
                self.view.show_message("Searching…")
            else:
                self.view.show_message(f"Nothing called “{self.search_text.strip()}” here")
        elif self.model.rowCount():
            self.view.show_message("Only hidden files here. Press Ctrl+H to show them.")
        elif self.at_trash:
            self.view.show_message("The trash is empty")
        elif self.in_recent:
            self.view.show_message("No recent files")
        else:
            self.view.show_message("This folder is empty")

    @property
    def in_trash(self):
        return self.location is not None and locations.in_trash(self.location)

    @property
    def read_only(self):
        """The trash and the recent files are for looking at: nothing is made or changed in them."""
        return self.in_trash or self.in_recent

    @property
    def in_recent(self):
        return self.location is not None and locations.in_recent(self.location)

    @property
    def at_trash(self):
        return self.location is not None and locations.is_trash(self.location)

    def selected_locations(self):
        return [
            locations.entry_location(self.location, info) for info in self.view.selected_infos()
        ]

    def close(self):
        if not self._closed:
            self._closed = True
            # The view may be gone before this is: nothing more is to be shown in it
            settings.changes.views_changed.disconnect(self._use_view)
            settings.changes.columns_changed.disconnect(self._use_columns)
        self.model.close()
