"""A Qt model of one folder: listed on a worker thread, then kept current by a monitor."""

import math
import time

from gi.repository import Gio, GLib
from PySide6.QtCore import (
    QAbstractTableModel,
    QLocale,
    QMimeData,
    QModelIndex,
    QObject,
    QRunnable,
    QSortFilterProxyModel,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QIcon, QPainter, QPixmap

from filehouse.core import locations, search
from filehouse.core.listing import (
    ATTRIBUTES,
    ListingError,
    count_children,
    folder_text,
    info_from_gio,
    list_directory,
    mode_text,
    natural_key,
    type_description,
)
from filehouse.integration import style
from filehouse.qt_models import columns, dates, thumbnails
from filehouse.qt_models.icons import icon_for_content_type

# One column for each of `columns.KEYS`, in that order
NAME, PERMISSIONS, MODIFIED, SIZE, TYPE, CREATED, ACCESSED, OWNER, GROUP, ORIGINAL, DELETED = range(
    len(columns.KEYS)
)
_HEADERS = [columns.TITLES[key] for key in columns.KEYS]

# Every column's text starts at its left edge, so the columns read as one grid
_ALIGNMENT = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter

# What a column shows of an entry: a date as seconds since the epoch, or a text
_DATES = {
    MODIFIED: lambda info: info.shown_date,
    CREATED: lambda info: info.created,
    ACCESSED: lambda info: info.accessed,
    DELETED: lambda info: info.deleted,
}
_TEXTS = {
    TYPE: lambda info: type_description(info.content_type),
    OWNER: lambda info: info.owner,
    GROUP: lambda info: info.group,
    ORIGINAL: lambda info: folder_text(info.original_path) if info.original_path else "",
}

# Folders that changed are counted again together, this long after the first of them
_COUNT_DELAY_MS = 300
# Seconds between two reports from a count of many folders
_COUNT_REPORT_S = 0.1

# The FileInfo behind a row, on any column
InfoRole = Qt.ItemDataRole.UserRole


class _ListingSignals(QObject):
    # Each carries the generation it belongs to, so results for a folder
    # the model has already left are dropped
    batch = Signal(int, object)
    finished = Signal(int)
    failed = Signal(int, str)
    counted = Signal(int, object)  # {file name: Count, or None for a folder that can't be read}
    counts_done = Signal(int)
    found = Signal(int, object)  # a batch of the results of a search
    search_finished = Signal(int, bool)  # whether it stopped at the limit


class _SearchTask(QRunnable):
    def __init__(self, generation, location, text, show_hidden, cancellable, signals):
        super().__init__()
        self._arguments = (generation, location, text, show_hidden, cancellable)
        self._signals = signals

    def run(self):
        generation, location, text, show_hidden, cancellable = self._arguments
        try:
            truncated = search.search_below(
                location,
                text,
                lambda batch: self._signals.found.emit(generation, batch),
                cancellable,
                show_hidden,
            )
            self._signals.search_finished.emit(generation, truncated)
        except RuntimeError:
            # The model was destroyed while this was still searching
            pass


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


class _CountTask(QRunnable):
    """Counts what is in each of the folders `names`, one after the other."""

    def __init__(self, generation, location, names, cancellable, signals):
        super().__init__()
        self._generation = generation
        self._location = location
        self._names = names
        self._cancellable = cancellable
        self._signals = signals

    def run(self):
        try:
            counts = {}
            reported = time.monotonic()
            for name in self._names:
                if self._cancellable.is_cancelled():
                    return
                counts[name] = count_children(self._location.get_child(name), self._cancellable)
                # A few at a time: one signal per folder would flood the UI thread
                if time.monotonic() - reported >= _COUNT_REPORT_S:
                    self._signals.counted.emit(self._generation, counts)
                    counts = {}
                    reported = time.monotonic()
            if counts:
                self._signals.counted.emit(self._generation, counts)
            self._signals.counts_done.emit(self._generation)
        except RuntimeError:
            # The model was destroyed while this was still counting
            pass


class DirectoryModel(QAbstractTableModel):
    loaded = Signal()
    load_failed = Signal(str)
    # A search below the folder started or ended
    search_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._infos = []
        # The row of each entry by its file name. Rows are only ever appended, so a row
        # given here is the entry's, or one above it for each row removed since: see
        # `row_for_name`
        self._rows = {}
        self._removed = 0
        self._location = None
        self._generation = 0
        self._cancellable = None
        self._monitor = None
        self._monitor_handler = None
        # Counts monitor events, to tell an answer that is older than a removal
        self._event = 0
        self._removed_at = {}
        self._locale = QLocale()
        self.loading = False
        # A search adds what it finds below the folder to the rows: entries whose name
        # is a path ("sub/report.pdf"). The folder's own entries are filtered by the proxy
        self._search_text = ""
        self._search_hidden = False
        self._search_generation = 0
        self._search_cancellable = None
        self.searching = False
        self.search_truncated = False
        # Thumbnails by file name: (modified, QIcon or None). None means there is none to show
        self._thumbnails = {}
        self._thumbnails_on = False
        self._thumbnailer = thumbnails.Thumbnailer(self)
        self._thumbnailer.ready.connect(self._on_thumbnail)
        # How many entries each folder has: Count, or None when it can't be read.
        # Counted after the listing, so the listing itself stays fast
        self._counts = {}
        self._to_count = set()
        self._counting = False
        self._count_hidden = False
        self._count_timer = QTimer(self)
        self._count_timer.setSingleShot(True)
        self._count_timer.setInterval(_COUNT_DELAY_MS)
        self._count_timer.timeout.connect(self._start_count)
        self._signals = _ListingSignals(self)
        self._signals.batch.connect(self._on_batch)
        self._signals.finished.connect(self._on_finished)
        self._signals.failed.connect(self._on_failed)
        self._signals.counted.connect(self._on_counted)
        self._signals.counts_done.connect(self._on_counts_done)
        self._signals.found.connect(self._on_found)
        self._signals.search_finished.connect(self._on_search_finished)

    # Loading

    def set_location(self, location):
        """Show a folder. Also reloads when given the current one."""
        self.close()
        self.beginResetModel()
        self._infos = []
        self._rows = {}
        self._removed = 0
        self._removed_at = {}
        self._thumbnails = {}
        self._counts = {}
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
        self._stop_search()
        self._set_search_text("")
        self._count_timer.stop()
        self._to_count = set()
        self._counting = False
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
            if info.name in self._rows:
                self._replace(info)
            else:
                new.append(info)
        self._append(new)

    def _on_finished(self, generation):
        if generation != self._generation:
            return
        self.loading = False
        self._start_count()
        self.loaded.emit()

    def _on_failed(self, generation, message):
        if generation != self._generation:
            return
        self.loading = False
        self.load_failed.emit(message)

    # Searching below the folder

    @property
    def search_text(self):
        return self._search_text

    def set_search(self, text, show_hidden=False):
        """Find what matches `text` in the folders below this one and add it to the
        rows; an empty text takes the results away again. Also searches again when
        given the text it has."""
        self._stop_search()
        self._remove_results()
        self._set_search_text(text)
        self._search_hidden = show_hidden
        if self._location is None or not search.words(text):
            self.search_changed.emit()
            return
        if search.can_search_below(self._location):
            self._search_cancellable = Gio.Cancellable()
            self.searching = True
            QThreadPool.globalInstance().start(
                _SearchTask(
                    self._search_generation,
                    self._location,
                    text,
                    show_hidden,
                    self._search_cancellable,
                    self._signals,
                )
            )
        self.search_changed.emit()

    def set_search_hidden(self, show_hidden):
        """Hidden files are shown or not from now on: a search follows."""
        if self._search_text and show_hidden != self._search_hidden:
            self.set_search(self._search_text, show_hidden)

    def _set_search_text(self, text):
        was = bool(self._search_text)
        self._search_text = text
        if was != bool(text):
            # The column that says where a result is
            self.headerDataChanged.emit(Qt.Orientation.Horizontal, PERMISSIONS, PERMISSIONS)

    def _stop_search(self):
        self._search_generation += 1
        self.searching = False
        self.search_truncated = False
        if self._search_cancellable is not None:
            self._search_cancellable.cancel()
            self._search_cancellable = None

    def _remove_results(self):
        rows = [row for row, info in enumerate(self._infos) if info.folder]
        # From the end, a stretch of rows at a time
        while rows:
            last = rows.pop()
            first = last
            while rows and rows[-1] == first - 1:
                first = rows.pop()
            self.beginRemoveRows(QModelIndex(), first, last)
            for info in self._infos[first : last + 1]:
                del self._rows[info.name]
                self._counts.pop(info.name, None)
                self._thumbnails.pop(info.name, None)
            del self._infos[first : last + 1]
            self._removed += last + 1 - first
            self.endRemoveRows()
        self._reindex_if_stale()

    def _on_found(self, generation, batch):
        if generation == self._search_generation:
            self._append([info for info in batch if info.name not in self._rows])

    def _on_search_finished(self, generation, truncated):
        if generation != self._search_generation:
            return
        self.searching = False
        self.search_truncated = truncated
        self._search_cancellable = None
        self.search_changed.emit()

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
        self._event += 1
        if event in (events.DELETED, events.MOVED_OUT):
            self._gone(file.get_basename())
        elif event == events.RENAMED:
            self._gone(file.get_basename())
            self._refresh(other_file)
        elif event in (
            events.CREATED,
            events.MOVED_IN,
            events.CHANGES_DONE_HINT,
            events.ATTRIBUTE_CHANGED,
        ):
            self._refresh(file)

    def _gone(self, name):
        self._removed_at[name] = self._event
        self._remove(name)

    def _refresh(self, file):
        file.query_info_async(
            ATTRIBUTES,
            Gio.FileQueryInfoFlags.NONE,
            GLib.PRIORITY_DEFAULT,
            self._cancellable,
            self._on_info,
            self._generation,
            self._event,
        )

    def _on_info(self, file, result, generation, asked_at):
        try:
            info = file.query_info_finish(result)
        except GLib.Error:
            # Gone again already, or the model moved on
            return
        if generation != self._generation:
            return
        info = info_from_gio(info)
        if self._removed_at.get(info.name, -1) >= asked_at:
            # Asked while the file was there, answered after it was renamed or deleted:
            # a file written under a temporary name and moved into place does this
            return
        if info.name in self._rows:
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
        self._rows.update((info.name, row) for row, info in enumerate(infos, first))
        self.endInsertRows()
        self._count_soon(info.name for info in infos if info.is_dir)

    def _replace(self, info):
        row = self.row_for_name(info.name)
        self._infos[row] = info
        if not info.is_dir:
            self._counts.pop(info.name, None)
        self.dataChanged.emit(self.index(row, 0), self.index(row, len(_HEADERS) - 1))
        if info.is_dir:
            # The count it has stays on show until the new one is in
            self._count_soon([info.name])

    def _remove(self, name):
        row = self.row_for_name(name)
        if row < 0:
            return
        self.beginRemoveRows(QModelIndex(), row, row)
        del self._infos[row]
        del self._rows[name]
        self._removed += 1
        self._counts.pop(name, None)
        self.endRemoveRows()
        self._reindex_if_stale()

    # Counting what is in the folders

    def recount(self):
        """Count every folder again: what is inside them can change without a word here."""
        self._count_soon(info.name for info in self._infos if info.is_dir)

    def set_count_hidden(self, on):
        """Whether hidden entries are part of a folder's count, as they are of the list."""
        if on == self._count_hidden:
            return
        self._count_hidden = on
        if self._infos:
            self.dataChanged.emit(self.index(0, SIZE), self.index(len(self._infos) - 1, SIZE))

    def item_count(self, name):
        """How many entries the folder `name` has, or None while that isn't known."""
        count = self._counts.get(name)
        if count is None:
            return None
        return count.shown + count.hidden if self._count_hidden else count.shown

    def _count_soon(self, names):
        if self._cancellable is None:
            # Closed: nothing is listed
            return
        self._to_count.update(names)
        if self._to_count and not self.loading and not self._counting:
            if not self._count_timer.isActive():
                self._count_timer.start()

    def _start_count(self):
        """One count at a time: what changes meanwhile waits for the next."""
        if self.loading or self._counting:
            return
        names = sorted(self._to_count & self._rows.keys(), key=natural_key)
        self._to_count = set()
        if not names:
            return
        self._counting = True
        QThreadPool.globalInstance().start(
            _CountTask(self._generation, self._location, names, self._cancellable, self._signals)
        )

    def _on_counted(self, generation, counts):
        if generation != self._generation:
            return
        rows = sorted(row for row in map(self.row_for_name, counts) if row >= 0)
        if not rows:
            return
        for row in rows:
            name = self._infos[row].name
            self._counts[name] = counts[name]
        # One signal for the stretch of rows they are in
        self.dataChanged.emit(self.index(rows[0], SIZE), self.index(rows[-1], SIZE))

    def _on_counts_done(self, generation):
        if generation != self._generation:
            return
        self._counting = False
        self._count_soon(())

    # Thumbnails

    def set_thumbnails(self, on):
        """Show pictures of image files in place of their type icon."""
        if on == self._thumbnails_on:
            return
        self._thumbnails_on = on
        if self._infos:
            self.dataChanged.emit(
                self.index(0, NAME),
                self.index(len(self._infos) - 1, NAME),
                [Qt.ItemDataRole.DecorationRole],
            )

    def _thumbnail(self, info):
        """The thumbnail of an entry if it is there. If not, it is asked for and comes later."""
        known = self._thumbnails.get(info.name)
        if known is not None and known[0] == info.modified:
            return known[1]
        target = locations.entry_location(self._location, info)
        path = locations.local_path(target)
        if path is None or info.is_dir:
            return None
        # Marked as "nothing to show" until the answer comes, so it is asked for once
        self._thumbnails[info.name] = (info.modified, None)
        self._thumbnailer.request(
            (self._generation, info.name, info.modified),
            target.get_uri(),
            path,
            info.modified,
            info.size,
            info.content_type,
        )
        return None

    def _on_thumbnail(self, token, image):
        generation, name, modified = token
        if generation != self._generation or image.isNull():
            return
        # On a square, so wide and tall pictures line up and their names sit at one height
        square = QPixmap(thumbnails.SIZE, thumbnails.SIZE)
        square.fill(Qt.GlobalColor.transparent)
        painter = QPainter(square)
        painter.drawImage(
            (thumbnails.SIZE - image.width()) // 2, (thumbnails.SIZE - image.height()) // 2, image
        )
        painter.end()
        self._thumbnails[name] = (modified, QIcon(square))
        row = self.row_for_name(name)
        if row >= 0:
            index = self.index(row, NAME)
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])

    def row_for_name(self, name):
        """The row of the entry with this file name, or -1."""
        known = self._rows.get(name)
        if known is None:
            return -1
        # Where it was when the index was made, or up to one row higher for each row
        # removed since: a look at a few rows. Searching from the start would make a
        # change to each of many entries go through all of them
        known = min(known, len(self._infos) - 1)
        for row in range(known, max(known - self._removed, 0) - 1, -1):
            if self._infos[row].name == name:
                return row
        # Unreachable while rows are only appended and removed
        self._reindex()
        return self._rows[name]

    def _reindex_if_stale(self):
        """After removals: the index again, before finding a row means looking far."""
        if self._removed > max(64, math.isqrt(len(self._infos))):
            self._reindex()

    def _reindex(self):
        self._rows = {info.name: row for row, info in enumerate(self._infos)}
        self._removed = 0

    def info(self, row):
        return self._infos[row]

    # QAbstractTableModel

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._infos)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(_HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation != Qt.Orientation.Horizontal:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            if section == PERMISSIONS and self._search_text:
                # Results come from many folders: where each is matters more
                return "Folder"
            return _HEADERS[section]
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _ALIGNMENT
        return None

    # Dragging out: the rows as `text/uri-list`

    def flags(self, index):
        flags = super().flags(index)
        if index.isValid():
            flags |= Qt.ItemFlag.ItemIsDragEnabled
        return flags

    def supportedDragActions(self):
        return Qt.DropAction.CopyAction | Qt.DropAction.MoveAction | Qt.DropAction.LinkAction

    def mimeTypes(self):
        return ["text/uri-list"]

    def mimeData(self, indexes):
        data = QMimeData()
        data.setUrls([QUrl(uri) for uri in self.uris({index.row() for index in indexes})])
        return data

    def uris(self, rows):
        """The URIs of the entries on these rows."""
        return [
            locations.entry_location(self._location, self._infos[row]).get_uri()
            for row in sorted(rows)
        ]

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
            if column == PERMISSIONS:
                return info.folder if self._search_text else mode_text(info.mode)
            if column in _TEXTS:
                return _TEXTS[column](info)
            if column in _DATES:
                return dates.friendly(_DATES[column](info), self._locale)
            if not info.is_dir:
                return self._locale.formattedDataSize(info.size)
            count = self.item_count(info.name)
            if count is None:
                return ""
            return "1 item" if count == 1 else f"{count} items"
        if role == Qt.ItemDataRole.DecorationRole and column == NAME:
            thumbnail = self._thumbnail(info) if self._thumbnails_on else None
            return thumbnail or icon_for_content_type(info.content_type)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return _ALIGNMENT
        if role == Qt.ItemDataRole.ForegroundRole and column != NAME:
            return style.color("textSecondary")
        if role == Qt.ItemDataRole.FontRole and column == PERMISSIONS and not self._search_text:
            return style.fixed_font()
        if role == Qt.ItemDataRole.ToolTipRole and info.folder:
            return locations.display_path(locations.entry_location(self._location, info))
        return None


class DirectorySortProxy(QSortFilterProxyModel):
    """Folders first, then natural order by name. Hides hidden files unless asked."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._show_hidden = False
        self._filter = []
        self._wanted = None

    @property
    def show_hidden(self):
        return self._show_hidden

    def set_filter_text(self, text):
        """Show only the entries whose name has every word of `text`."""
        wanted = search.words(text)
        if wanted == self._filter:
            return
        self.beginFilterChange()
        self._filter = wanted
        self.endFilterChange()

    def set_wanted(self, wanted):
        """Show only the files `wanted(info)` is true for, as a file dialog does with
        the types an app asked for. Folders are shown all the same. None shows all."""
        self.beginFilterChange()
        self._wanted = wanted
        self.endFilterChange()

    def set_show_hidden(self, show):
        if show == self._show_hidden:
            return
        self.beginFilterChange()
        self._show_hidden = show
        self.endFilterChange()
        self.sourceModel().set_count_hidden(show)
        self.sourceModel().set_search_hidden(show)

    def filterAcceptsRow(self, source_row, source_parent):
        info = self.sourceModel().info(source_row)
        if info.is_hidden and not self._show_hidden:
            return False
        if self._wanted is not None and not info.is_dir and not self._wanted(info):
            return False
        return not self._filter or search.matches(self._filter, info.display_name)

    def lessThan(self, left, right):
        model = self.sourceModel()
        a = model.info(left.row())
        b = model.info(right.row())
        if a.is_dir != b.is_dir:
            # Folders stay on top in both sort directions
            return a.is_dir == (self.sortOrder() == Qt.SortOrder.AscendingOrder)
        column = left.column()
        if column == SIZE:
            if a.is_dir:
                # Folders by how much is in them; one not counted yet comes first
                size_a, size_b = (model.item_count(info.name) or 0 for info in (a, b))
            else:
                size_a, size_b = a.size, b.size
            if size_a != size_b:
                return size_a < size_b
        if column in _DATES:
            when_a, when_b = _DATES[column](a), _DATES[column](b)
            if when_a != when_b:
                return when_a < when_b
        if column in _TEXTS:
            text_a, text_b = natural_key(_TEXTS[column](a)), natural_key(_TEXTS[column](b))
            if text_a != text_b:
                return text_a < text_b
        if column == PERMISSIONS and model.search_text:
            if a.folder != b.folder:
                return natural_key(a.folder) < natural_key(b.folder)
        elif column == PERMISSIONS and a.mode != b.mode:
            return (a.mode or 0) < (b.mode or 0)
        return a.sort_key < b.sort_key
