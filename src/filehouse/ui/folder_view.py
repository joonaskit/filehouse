"""The file list, as details (a table) or as icons with thumbnails."""

import time

from PySide6.QtCore import QEvent, QItemSelection, QItemSelectionModel, QPoint, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHeaderView,
    QLabel,
    QListView,
    QStackedLayout,
    QStyle,
    QStyledItemDelegate,
    QTreeView,
    QWidget,
)

from filehouse.integration import style
from filehouse.qt_models.directory_model import MODIFIED, NAME, PERMISSIONS, SIZE, InfoRole
from filehouse.ui import clipboard, settings


class _TakesDrops:
    """For an item view: takes drops of files. What a drop does is decided by the window.

    The view declares `dropped`: URIs, the FileInfo of the folder dropped on (None for
    the open folder), the keyboard modifiers and a global position, `focused`, and
    `open_in_tab`: the FileInfo of a folder that was middle-clicked."""

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.MiddleButton:
            index = self.indexAt(event.position().toPoint())
            info = index.data(InfoRole) if index.isValid() else None
            if info is not None and info.is_dir:
                self.open_in_tab.emit(info)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.focused.emit()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        # The base class scrolls near the edges, and then refuses the drop
        super().dragMoveEvent(event)
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        uris, _cut = clipboard.read(event.mimeData())
        if not uris:
            event.ignore()
            return
        position = event.position().toPoint()
        index = self.indexAt(position)
        info = index.data(InfoRole) if index.isValid() else None
        # The files are moved or copied here, by a job. Answering "copy" keeps
        # the app they came from from removing anything itself
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        self.dropped.emit(
            uris,
            info if info is not None and info.is_dir else None,
            event.modifiers(),
            self.viewport().mapToGlobal(position),
        )


class _Rows(QStyledItemDelegate):
    """Draws entries without the style's own frame around the one the keyboard is on.
    What is selected is shown by the selection alone."""

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.state &= ~QStyle.StateFlag.State_HasFocus


class _Tree(_TakesDrops, QTreeView):
    dropped = Signal(list, object, object, QPoint)
    focused = Signal()
    open_in_tab = Signal(object)


class _Grid(_TakesDrops, QListView):
    dropped = Signal(list, object, object, QPoint)
    focused = Signal()
    open_in_tab = Signal(object)


class FolderView(QWidget):
    item_activated = Signal(object)  # FileInfo
    dropped = Signal(list, object, object, QPoint)  # see _TakesDrops.dropped
    # FileInfo, or None for the empty area, and a global position
    context_menu_requested = Signal(object, QPoint)
    # The list, or the empty folder in its place, was clicked or given the keyboard
    focused = Signal()
    open_in_tab = Signal(object)  # FileInfo of a folder
    # The column and the order the list is sorted by changed, by a click or by `sort`
    sort_changed = Signal(int, Qt.SortOrder)

    def __init__(self, proxy, parent=None):
        super().__init__(parent)
        self.setObjectName("pane")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setProperty("split", False)
        self.setProperty("active", True)
        self._proxy = proxy
        self._mode = settings.DETAILS
        self._typed_at = None  # when a name was last typed at the list, to jump to it

        self._tree = _Tree()
        self._tree.setModel(proxy)
        self._tree.setRootIsDecorated(False)
        self._tree.setItemsExpandable(False)
        self._tree.setUniformRowHeights(True)
        # False: with it the tree frames the whole row the keyboard is on, selected or not
        self._tree.setAllColumnsShowFocus(False)
        self._tree.setIconSize(
            QSize(style.px(style.dense("list_icon")), style.px(style.dense("list_icon")))
        )
        self._tree.setSortingEnabled(True)
        self._tree.sortByColumn(NAME, Qt.SortOrder.AscendingOrder)

        header = self._tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(NAME, QHeaderView.ResizeMode.Stretch)
        for column, width in ((PERMISSIONS, 100), (MODIFIED, 120), (SIZE, 90)):
            header.resizeSection(column, style.px(width))
        header.setSortIndicatorClearable(False)
        header.sortIndicatorChanged.connect(self.sort_changed)

        self._grid = _Grid()
        self._grid.setModel(proxy)
        self._grid.setModelColumn(NAME)
        # One selection for both, so changing the mode keeps it
        self._grid.setSelectionModel(self._tree.selectionModel())
        self._grid.setViewMode(QListView.ViewMode.IconMode)
        # Icon mode lets items be dragged around inside the view by default
        self._grid.setMovement(QListView.Movement.Static)
        self._grid.setResizeMode(QListView.ResizeMode.Adjust)
        # Laid out a batch at a time, so a large folder doesn't freeze the window
        self._grid.setLayoutMode(QListView.LayoutMode.Batched)
        self._grid.setWordWrap(True)
        self._grid.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self._grid.setIconSize(
            QSize(style.px(style.dense("grid_icon")), style.px(style.dense("grid_icon")))
        )
        self._grid.setGridSize(
            QSize(style.px(style.dense("grid_width")), style.px(style.dense("grid_height")))
        )
        self._grid.setSpacing(style.px(4))

        for view in (self._tree, self._grid):
            view.setItemDelegate(_Rows(view))
            view.installEventFilter(self)
            view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
            view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            view.setDragEnabled(True)
            view.setAcceptDrops(True)
            view.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
            view.setDefaultDropAction(Qt.DropAction.MoveAction)
            view.dropped.connect(self.dropped)
            view.focused.connect(self.focused)
            view.open_in_tab.connect(self.open_in_tab)
            view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            view.customContextMenuRequested.connect(
                lambda position, view=view: self._on_context_menu(view, position)
            )
            # `activated` follows the desktop's single or double click setting, and Enter
            view.activated.connect(self._on_activated)

        self._message = QLabel()
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message.setWordWrap(True)
        self._message.setEnabled(False)
        self._message.setAutoFillBackground(True)
        self._message.setBackgroundRole(self._tree.viewport().backgroundRole())

        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.addWidget(self._tree)
        self._stack.addWidget(self._grid)
        self._stack.addWidget(self._message)
        self.setFocusProxy(self._tree)
        # For the message shown in place of the list: an empty folder takes drops
        # and has a context menu too
        self.setAcceptDrops(True)

    def set_split(self, split, active):
        """Beside another list: framed, and in the accent colour while it is the active one."""
        self.setProperty("split", split)
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event):
        # The message of an empty folder has nothing to take the click
        self.setFocus()
        self.focused.emit()
        super().mousePressEvent(event)

    @property
    def mode(self):
        return self._mode

    def set_mode(self, mode):
        """`settings.DETAILS` or `settings.ICONS`."""
        if mode == self._mode:
            return
        showing_items = self._stack.currentWidget() is not self._message
        had_focus = self._items().hasFocus()
        self._mode = mode
        # Thumbnails are for the mode with icons large enough to show them
        self._proxy.sourceModel().set_thumbnails(mode == settings.ICONS)
        self.setFocusProxy(self._items())
        if showing_items:
            self.show_items()
        if had_focus:
            self._items().setFocus()
        current = self.selection_model().currentIndex()
        if current.isValid():
            self._items().scrollTo(current)

    # Typing a name jumps to it (Qt's keyboard search). Space is the window's key for the
    # preview, but in the middle of a name it is a part of the name

    def is_searching(self):
        """Whether a name is being typed at the list: a key of it came a moment ago."""
        if self._typed_at is None:
            return False
        waited = (time.monotonic() - self._typed_at) * 1000
        return waited < QApplication.keyboardInputInterval()

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind not in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
            return False
        if event.key() == Qt.Key.Key_Space:
            if not self.is_searching():
                return False
            if kind == QEvent.Type.KeyPress:
                watched.keyboardSearch(" ")
                self._typed_at = time.monotonic()
            else:
                # Ours, not the shortcut's: the key press comes here next
                event.accept()
            return True
        if kind == QEvent.Type.KeyPress and self._starts_a_name(event):
            self._typed_at = time.monotonic()
        return False

    @staticmethod
    def _starts_a_name(event):
        commands = (
            Qt.KeyboardModifier.ControlModifier
            | Qt.KeyboardModifier.AltModifier
            | Qt.KeyboardModifier.MetaModifier
        )
        text = event.text()
        return bool(text) and text.isprintable() and not (event.modifiers() & commands)

    def _items(self):
        return self._grid if self._mode == settings.ICONS else self._tree

    def show_items(self):
        self._stack.setCurrentWidget(self._items())

    def show_message(self, text):
        """Replace the list with a message, for an empty or unreadable folder."""
        self._message.setText(text)
        self._stack.setCurrentWidget(self._message)

    def select_name(self, name):
        """Select the entry with this file name and scroll to it. False when the list
        does not show it (yet)."""
        row = self._proxy.sourceModel().row_for_name(name)
        if row < 0:
            return False
        index = self._proxy.mapFromSource(self._proxy.sourceModel().index(row, NAME))
        if not index.isValid():
            return False
        self.selection_model().setCurrentIndex(
            index,
            QItemSelectionModel.SelectionFlag.ClearAndSelect
            | QItemSelectionModel.SelectionFlag.Rows,
        )
        self._items().scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)
        return True

    def _selected_rows(self):
        # Not selectedRows(): the icons only ever select the name column
        return sorted({index.row() for index in self.selection_model().selectedIndexes()})

    def selected_count(self):
        return len(self._selected_rows())

    def selected_infos(self):
        """The selected entries, in the order they are shown."""
        return [self._proxy.index(row, NAME).data(InfoRole) for row in self._selected_rows()]

    def selection_model(self):
        return self._tree.selectionModel()

    def set_compact(self, on):
        """For a narrow window: the table without its Modified column."""
        self._tree.setColumnHidden(MODIFIED, on)

    def show_current(self):
        """Scroll as little as it takes to have the current entry in view."""
        current = self.selection_model().currentIndex()
        if current.isValid():
            self._items().scrollTo(current)

    def scroll_position(self):
        """How far down the list is scrolled, to compare before and after a change."""
        return self._items().verticalScrollBar().value()

    def select_all(self):
        self._items().selectAll()

    def info_at(self, row):
        """The entry shown in a row of the list."""
        return self._proxy.index(row, NAME).data(InfoRole)

    def select_where(self, wanted, add=False):
        """Select the entries `wanted(info)` is true for, in place of what is selected
        or with `add` beside it. Returns how many it picked."""
        rows = [row for row in range(self._proxy.rowCount()) if wanted(self.info_at(row))]
        self._select_rows(rows, add)
        return len(rows)

    def invert_selection(self):
        """Select what is not selected, and nothing else."""
        selected = set(self._selected_rows())
        self._select_rows([row for row in range(self._proxy.rowCount()) if row not in selected])

    def _select_rows(self, rows, add=False):
        selection = QItemSelection()
        last = self._proxy.columnCount() - 1
        start = previous = None
        # One range for each run of rows: a folder of thousands is a few ranges
        for row in [*rows, None]:
            if start is not None and row != previous + 1:
                selection.select(self._proxy.index(start, 0), self._proxy.index(previous, last))
                start = None
            if start is None:
                start = row
            previous = row
        flags = QItemSelectionModel.SelectionFlag
        self.selection_model().select(selection, flags.Select if add else flags.ClearAndSelect)
        if rows:
            first = self._proxy.index(rows[0], NAME)
            self.selection_model().setCurrentIndex(first, flags.NoUpdate)
            self._items().scrollTo(first)

    # The order is the table's: its header shows it, and the icons follow the same proxy

    def sort_column(self):
        return self._tree.header().sortIndicatorSection()

    def sort_order(self):
        return self._tree.header().sortIndicatorOrder()

    def sort(self, column, order):
        self._tree.sortByColumn(column, order)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        uris, _cut = clipboard.read(event.mimeData())
        if uris:
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            position = self.mapToGlobal(event.position().toPoint())
            self.dropped.emit(uris, None, event.modifiers(), position)

    def contextMenuEvent(self, event):
        self.context_menu_requested.emit(None, event.globalPos())

    def _on_activated(self, index):
        self.item_activated.emit(index.data(InfoRole))

    def _on_context_menu(self, view, position):
        index = view.indexAt(position)
        info = index.data(InfoRole) if index.isValid() else None
        self.context_menu_requested.emit(info, view.viewport().mapToGlobal(position))
