"""A Qt model of one folder: listed on a worker thread, then kept current by a monitor."""

from gi.repository import Gio, GLib
from PySide6.QtCore import (
    QAbstractTableModel,
    QDateTime,
    QLocale,
    QModelIndex,
    QObject,
    QRunnable,
    QSortFilterProxyModel,
    Qt,
    QThreadPool,
    Signal,
)

from filehouse.core.listing import ATTRIBUTES, ListingError, info_from_gio, list_directory
from filehouse.qt_models.icons import description_for_content_type, icon_for_content_type

NAME, SIZE, MODIFIED, TYPE = range(4)
_HEADERS = ["Name", "Size", "Modified", "Type"]

# The FileInfo behind a row, on any column
InfoRole = Qt.ItemDataRole.UserRole


class _ListingSignals(QObject):
    # Each carries the generation it belongs to, so results for a folder
    # the model has already left are dropped
    batch = Signal(int, object)
    finished = Signal(int)
    failed = Signal(int, str)


class _ListingTask(QRunnable):
    def __init__(self, generation, location, cancellable, signals):
        super().__init__()
        self._generation = generation
        self._location = location
        self._cancellable = cancellable
        self._signals = signals

    def run(self):
        try:
            list_directory(
                self._location,
                lambda batch: self._signals.batch.emit(self._generation, batch),
                self._cancellable,
            )
            self._signals.finished.emit(self._generation)
        except ListingError as error:
            self._signals.failed.emit(self._generation, str(error))
        except RuntimeError:
            # The model was destroyed while this was still listing
            pass


class DirectoryModel(QAbstractTableModel):
    loaded = Signal()
    load_failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._infos = []
        self._names = set()
        self._location = None
        self._generation = 0
        self._cancellable = None
        self._monitor = None
        self._monitor_handler = None
        self._locale = QLocale()
        self.loading = False
        self._signals = _ListingSignals(self)
        self._signals.batch.connect(self._on_batch)
        self._signals.finished.connect(self._on_finished)
        self._signals.failed.connect(self._on_failed)

    # Loading

    def set_location(self, location):
        """Show a folder. Also reloads when given the current one."""
        self.close()
        self.beginResetModel()
        self._infos = []
        self._names = set()
        self.endResetModel()

        self._location = location
        self._cancellable = Gio.Cancellable()
        self.loading = True
        # Monitor first, so nothing created during the listing is missed.
        # Entries seen twice are merged by name.
        self._start_monitor()
        QThreadPool.globalInstance().start(
            _ListingTask(self._generation, location, self._cancellable, self._signals)
        )

    def close(self):
        """Stop listing and monitoring. Safe to call more than once."""
        self._generation += 1
        self.loading = False
        if self._cancellable is not None:
            self._cancellable.cancel()
            self._cancellable = None
        if self._monitor is not None:
            self._monitor.disconnect(self._monitor_handler)
            self._monitor.cancel()
            self._monitor = None

    def _on_batch(self, generation, batch):
        if generation != self._generation:
            return
        new = []
        for info in batch:
            if info.name in self._names:
                self._replace(info)
            else:
                new.append(info)
        self._append(new)

    def _on_finished(self, generation):
        if generation != self._generation:
            return
        self.loading = False
        self.loaded.emit()

    def _on_failed(self, generation, message):
        if generation != self._generation:
            return
        self.loading = False
        self.load_failed.emit(message)

    # Following changes

    def _start_monitor(self):
        try:
            self._monitor = self._location.monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
        except GLib.Error:
            # Not every location can be monitored; the folder still lists
            self._monitor = None
            return
        self._monitor_handler = self._monitor.connect("changed", self._on_changed)

    def _on_changed(self, _monitor, file, other_file, event):
        events = Gio.FileMonitorEvent
        if event in (events.DELETED, events.MOVED_OUT):
            self._remove(file.get_basename())
        elif event == events.RENAMED:
            self._remove(file.get_basename())
            self._refresh(other_file)
        elif event in (
            events.CREATED,
            events.MOVED_IN,
            events.CHANGES_DONE_HINT,
            events.ATTRIBUTE_CHANGED,
        ):
            self._refresh(file)

    def _refresh(self, file):
        file.query_info_async(
            ATTRIBUTES,
            Gio.FileQueryInfoFlags.NONE,
            GLib.PRIORITY_DEFAULT,
            self._cancellable,
            self._on_info,
            self._generation,
        )

    def _on_info(self, file, result, generation):
        try:
            info = file.query_info_finish(result)
        except GLib.Error:
            # Gone again already, or the model moved on
            return
        if generation != self._generation:
            return
        info = info_from_gio(info)
        if info.name in self._names:
            self._replace(info)
        else:
            self._append([info])

    # Rows

    def _append(self, infos):
        if not infos:
            return
        first = len(self._infos)
        self.beginInsertRows(QModelIndex(), first, first + len(infos) - 1)
        self._infos.extend(infos)
        self._names.update(info.name for info in infos)
        self.endInsertRows()

    def _replace(self, info):
        row = self.row_for_name(info.name)
        self._infos[row] = info
        self.dataChanged.emit(self.index(row, 0), self.index(row, len(_HEADERS) - 1))

    def _remove(self, name):
        if name not in self._names:
            return
        row = self.row_for_name(name)
        self.beginRemoveRows(QModelIndex(), row, row)
        del self._infos[row]
        self._names.discard(name)
        self.endRemoveRows()

    def row_for_name(self, name):
        """The row of the entry with this file name, or -1."""
        if name not in self._names:
            return -1
        return next(row for row, info in enumerate(self._infos) if info.name == name)

    def info(self, row):
        return self._infos[row]

    # QAbstractTableModel

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._infos)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(_HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return _HEADERS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        info = self._infos[index.row()]
        column = index.column()
        if role == InfoRole:
            return info
        if role == Qt.ItemDataRole.DisplayRole:
            if column == NAME:
                return info.display_name
            if column == SIZE:
                return "" if info.is_dir else self._locale.formattedDataSize(info.size)
            if column == MODIFIED:
                if not info.modified:
                    return ""
                return self._locale.toString(
                    QDateTime.fromSecsSinceEpoch(info.modified), QLocale.FormatType.ShortFormat
                )
            return description_for_content_type(info.content_type)
        if role == Qt.ItemDataRole.DecorationRole and column == NAME:
            return icon_for_content_type(info.content_type)
        if role == Qt.ItemDataRole.TextAlignmentRole and column == SIZE:
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        return None


class DirectorySortProxy(QSortFilterProxyModel):
    """Folders first, then natural order by name. Hides hidden files unless asked."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._show_hidden = False

    @property
    def show_hidden(self):
        return self._show_hidden

    def set_show_hidden(self, show):
        if show == self._show_hidden:
            return
        self.beginFilterChange()
        self._show_hidden = show
        self.endFilterChange()

    def filterAcceptsRow(self, source_row, source_parent):
        return self._show_hidden or not self.sourceModel().info(source_row).is_hidden

    def lessThan(self, left, right):
        model = self.sourceModel()
        a = model.info(left.row())
        b = model.info(right.row())
        if a.is_dir != b.is_dir:
            # Folders stay on top in both sort directions
            return a.is_dir == (self.sortOrder() == Qt.SortOrder.AscendingOrder)
        column = left.column()
        if column == SIZE and a.size != b.size:
            return a.size < b.size
        if column == MODIFIED and a.modified != b.modified:
            return a.modified < b.modified
        if column == TYPE:
            type_a = description_for_content_type(a.content_type).casefold()
            type_b = description_for_content_type(b.content_type).casefold()
            if type_a != type_b:
                return type_a < type_b
        return a.sort_key < b.sort_key
