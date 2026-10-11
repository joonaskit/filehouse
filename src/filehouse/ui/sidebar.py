"""Places, and below them the drives and other volumes that are plugged in."""

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QMenu

from filehouse.core import locations
from filehouse.integration import style

_EntryRole = Qt.ItemDataRole.UserRole


class Sidebar(QListWidget):
    place_activated = Signal(object)  # a location

    def __init__(self, places, volumes, parent=None):
        """`places` is a list of Place, `volumes` the Volumes to show and follow."""
        super().__init__(parent)
        self._places = places
        self._volumes = volumes
        self._selected = None
        self.setIconSize(QSize(style.px(18), style.px(18)))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.itemClicked.connect(self._on_clicked)
        volumes.changed.connect(self._fill)
        self._fill()

    def entries(self):
        """Every Place and Volume shown, top to bottom."""
        return [self.item(row).data(_EntryRole) for row in range(self.count())]

    def select(self, location):
        """Highlight the entry for `location`, or nothing when there is none."""
        self._selected = location
        self.setCurrentRow(
            next(
                (
                    row
                    for row, entry in enumerate(self.entries())
                    if locations.same(entry.location, location)
                ),
                -1,
            )
        )

    def _fill(self):
        self.clear()
        for entry in (*self._places, *self._volumes.entries()):
            item = QListWidgetItem(QIcon.fromTheme(entry.icon), entry.name)
            item.setData(_EntryRole, entry)
            if entry.location is None:
                item.setToolTip("Not opened yet. Click to open it.")
            self.addItem(item)
        self.select(self._selected)

    def _on_clicked(self, item):
        entry = item.data(_EntryRole)
        if entry.location is not None:
            self.place_activated.emit(entry.location)
        else:
            # A drive that is plugged in but not mounted: mount it, then go there
            self._volumes.mount(entry, self.place_activated.emit)

    def _on_context_menu(self, position):
        item = self.itemAt(position)
        entry = item.data(_EntryRole) if item is not None else None
        if entry is None or not getattr(entry, "can_eject", False):
            return
        menu = QMenu(self)
        menu.addAction(
            QIcon.fromTheme("media-eject"), entry.eject_label, lambda: self._volumes.eject(entry)
        )
        menu.exec(self.viewport().mapToGlobal(position))
