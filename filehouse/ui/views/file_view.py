"""Main directory content view component (table view)."""

from pathlib import Path
from PySide6.QtCore import Signal, QDir, QModelIndex, Qt
from PySide6.QtWidgets import (
    QTableView,
    QHeaderView,
    QMenu,
    QFileSystemModel,
)
from PySide6.QtGui import QAction

from filehouse.core.file_ops import open_file_with_default_app


class FileTableView(QTableView):
    """Table view displaying files and folders in the current directory."""

    directory_opened = Signal(Path)
    file_activated = Signal(Path)
    selection_changed = Signal(list)  # list of selected Paths

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model = QFileSystemModel(self)
        self.model.setFilter(QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot)
        self.model.setRootPath("")

        self.setModel(self.model)

        # Visual configuration
        self.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.setSortingEnabled(True)
        self.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.setShowGrid(False)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setHighlightSections(False)
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)

        # Context menu
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        # Connect activation & selection
        self.doubleClicked.connect(self._on_double_clicked)
        self.selectionModel().selectionChanged.connect(self._on_selection_changed)

    def navigate_to(self, path: Path) -> None:
        """Point table view to the specified directory."""
        index = self.model.setRootPath(str(path))
        self.setRootIndex(index)
        self.horizontalHeader().resizeSection(0, 260)  # Name
        self.horizontalHeader().resizeSection(1, 90)   # Size
        self.horizontalHeader().resizeSection(2, 120)  # Type

    def set_show_hidden(self, show: bool) -> None:
        """Toggle display of hidden items."""
        filters = QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot
        if show:
            filters |= QDir.Filter.Hidden
        self.model.setFilter(filters)

    def get_selected_paths(self) -> list[Path]:
        """Return list of selected Paths."""
        rows = self.selectionModel().selectedRows()
        return [Path(self.model.filePath(idx)) for idx in rows]

    def _on_double_clicked(self, index: QModelIndex) -> None:
        file_path = Path(self.model.filePath(index))
        if self.model.isDir(index):
            self.directory_opened.emit(file_path)
        else:
            self.file_activated.emit(file_path)

    def _on_selection_changed(self, selected, deselected) -> None:
        self.selection_changed.emit(self.get_selected_paths())

    def _show_context_menu(self, pos) -> None:
        selected_paths = self.get_selected_paths()
        if not selected_paths:
            return

        menu = QMenu(self)
        target = selected_paths[0]

        if target.is_dir():
            open_action = QAction("Open Folder", self)
            open_action.triggered.connect(lambda: self.directory_opened.emit(target))
            menu.addAction(open_action)
        else:
            open_action = QAction("Open with Default Application", self)
            open_action.triggered.connect(lambda: open_file_with_default_app(target))
            menu.addAction(open_action)

        menu.addSeparator()

        copy_path_action = QAction("Copy Path to Clipboard", self)
        copy_path_action.triggered.connect(lambda: self._copy_to_clipboard(str(target)))
        menu.addAction(copy_path_action)

        menu.exec(self.viewport().mapToGlobal(pos))

    def _copy_to_clipboard(self, text: str) -> None:
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(text)
