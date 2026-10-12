"""One folder in a window: its model, sort order, list, history and selection."""

from PySide6.QtCore import QObject, Qt, Signal

from filehouse.core import locations
from filehouse.core.history import History
from filehouse.qt_models.directory_model import MODIFIED, DirectoryModel, DirectorySortProxy
from filehouse.ui.folder_view import FolderView


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
        self._sort_before_recent = None

        self.model = DirectoryModel(self)
        self.proxy = DirectorySortProxy(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.set_show_hidden(show_hidden)
        self.model.loaded.connect(self._on_loaded)
        self.model.load_failed.connect(self._on_load_failed)
        self.view = FolderView(self.proxy)
        self.view.selection_model().selectionChanged.connect(self.changed)

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
        self.show(self.location)

    def show(self, location, select=None):
        self._sort_for(location)
        self.location = location
        self._pending_select = select
        self.error = None
        self.model.set_location(location)
        self.location_changed.emit(location)

    def _sort_for(self, location):
        """The recent files are shown the latest first, and the sort that was chosen
        comes back when the pane leaves them."""
        now = locations.in_recent(location)
        before = self.location is not None and locations.in_recent(self.location)
        if now and not before:
            self._sort_before_recent = (self.view.sort_column(), self.view.sort_order())
            self.view.sort(MODIFIED, Qt.SortOrder.DescendingOrder)
        elif before and not now and self._sort_before_recent is not None:
            self.view.sort(*self._sort_before_recent)
            self._sort_before_recent = None

    @staticmethod
    def _name_within(location, folder):
        """The name of `location` if it sits directly in `folder`, to select it there."""
        if locations.same(locations.parent(location), folder):
            return locations.basename(location)
        return None

    # State

    def select_when_listed(self, folder, name):
        """Select an entry that something made or renamed, once it is listed."""
        if locations.same(folder, self.location):
            self._pending_select = name
            self.changed.emit()

    def _on_loaded(self):
        self.select_pending()
        self.changed.emit()

    def select_pending(self):
        name = self._pending_select
        if name and self.model.row_for_name(name) >= 0:
            self.view.select_name(name)
            self._pending_select = None

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
        self.model.close()
