"""Sidebar directory tree view component."""

from pathlib import Path
from PySide6.QtCore import Signal, QDir, QModelIndex, Qt
from PySide6.QtWidgets import QTreeView, QFileSystemModel


class DirectoryTreeView(QTreeView):
    """Sidebar tree view listing folders for quick navigation."""

    directory_selected = Signal(Path)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model = QFileSystemModel(self)
        self.model.setFilter(QDir.Filter.Dirs | QDir.Filter.NoDotAndDotDot | QDir.Filter.AllDirs)
        self.model.setRootPath("")

        self.setModel(self.model)

        # Hide Size, Type, Date columns in sidebar tree to keep it clean
        self.setHeaderHidden(False)
        self.header().setStretchLastSection(False)
        for col in range(1, self.model.columnCount()):
            self.hideColumn(col)

        self.setAnimated(True)
        self.setIndentation(16)
        self.setSortingEnabled(True)
        self.sortByColumn(0, Qt.SortOrder.AscendingOrder)

        self.clicked.connect(self._on_item_clicked)

    def set_show_hidden(self, show: bool) -> None:
        """Toggle display of hidden directories."""
        filters = QDir.Filter.Dirs | QDir.Filter.NoDotAndDotDot | QDir.Filter.AllDirs
        if show:
            filters |= QDir.Filter.Hidden
        self.model.setFilter(filters)

    def select_path(self, path: Path) -> None:
        """Expand and highlight the given path in the tree."""
        index = self.model.index(str(path))
        if index.isValid():
            self.setCurrentIndex(index)
            self.scrollTo(index)

    def _on_item_clicked(self, index: QModelIndex) -> None:
        file_path = self.model.filePath(index)
        if file_path:
            self.directory_selected.emit(Path(file_path))
