"""The file list. Details mode only for now; the icons mode comes with thumbnails."""

from PySide6.QtCore import QItemSelectionModel, QPoint, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QStackedLayout,
    QTreeView,
    QWidget,
)

from filehouse.integration import style
from filehouse.qt_models.directory_model import MODIFIED, NAME, SIZE, TYPE, InfoRole
from filehouse.ui import clipboard


class _Tree(QTreeView):
    """The tree, taking drops of files. What a drop does is decided by the window."""

    # URIs, the FileInfo of the folder dropped on (None for the open folder),
    # the keyboard modifiers and a global position
    dropped = Signal(list, object, object, QPoint)

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


class FolderView(QWidget):
    item_activated = Signal(object)  # FileInfo
    dropped = Signal(list, object, object, QPoint)  # see _Tree.dropped
    # FileInfo, or None for the empty area, and a global position
    context_menu_requested = Signal(object, QPoint)

    def __init__(self, proxy, parent=None):
        super().__init__(parent)
        self._proxy = proxy

        self._tree = _Tree()
        self._tree.setModel(proxy)
        self._tree.setDragEnabled(True)
        self._tree.setAcceptDrops(True)
        self._tree.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self._tree.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._tree.dropped.connect(self.dropped)
        self._tree.setRootIsDecorated(False)
        self._tree.setItemsExpandable(False)
        self._tree.setUniformRowHeights(True)
        self._tree.setAllColumnsShowFocus(True)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._tree.setIconSize(QSize(style.px(20), style.px(20)))
        self._tree.setSortingEnabled(True)
        self._tree.sortByColumn(NAME, Qt.SortOrder.AscendingOrder)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)
        # `activated` follows the desktop's single or double click setting, and Enter
        self._tree.activated.connect(self._on_activated)

        header = self._tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(NAME, QHeaderView.ResizeMode.Stretch)
        for column, width in ((SIZE, 90), (MODIFIED, 150), (TYPE, 170)):
            header.resizeSection(column, style.px(width))

        self._message = QLabel()
        self._message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message.setWordWrap(True)
        self._message.setEnabled(False)
        self._message.setAutoFillBackground(True)
        self._message.setBackgroundRole(self._tree.viewport().backgroundRole())

        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.addWidget(self._tree)
        self._stack.addWidget(self._message)
        self.setFocusProxy(self._tree)
        # For the message shown in place of the list: an empty folder takes drops
        # and has a context menu too
        self.setAcceptDrops(True)

    def show_items(self):
        self._stack.setCurrentWidget(self._tree)

    def show_message(self, text):
        """Replace the list with a message, for an empty or unreadable folder."""
        self._message.setText(text)
        self._stack.setCurrentWidget(self._message)

    def select_name(self, name):
        """Select the entry with this file name and scroll to it."""
        row = self._proxy.sourceModel().row_for_name(name)
        if row < 0:
            return
        index = self._proxy.mapFromSource(self._proxy.sourceModel().index(row, NAME))
        if not index.isValid():
            return
        self._tree.selectionModel().setCurrentIndex(
            index,
            QItemSelectionModel.SelectionFlag.ClearAndSelect
            | QItemSelectionModel.SelectionFlag.Rows,
        )
        self._tree.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)

    def selected_count(self):
        return len(self._tree.selectionModel().selectedRows())

    def selected_infos(self):
        """The selected entries, in the order they are shown."""
        rows = sorted(self._tree.selectionModel().selectedRows(), key=lambda index: index.row())
        return [index.data(InfoRole) for index in rows]

    def selection_model(self):
        return self._tree.selectionModel()

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

    def _on_context_menu(self, position):
        index = self._tree.indexAt(position)
        info = index.data(InfoRole) if index.isValid() else None
        self.context_menu_requested.emit(info, self._tree.viewport().mapToGlobal(position))
