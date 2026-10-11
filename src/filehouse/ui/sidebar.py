"""Places, and below them the drives and other volumes that are plugged in."""

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QListWidget, QListWidgetItem

from filehouse.core import locations
from filehouse.integration import style
from filehouse.ui import menus

_EntryRole = Qt.ItemDataRole.UserRole

# The palette colour of a place's tile, by its icon. Anything else is a plain folder
_PLACE_COLOURS = {
    "user-home": "blue",
    "user-desktop": "indigo",
    "folder-documents": "yellow",
    "folder-download": "green",
    "folder-pictures": "purple",
    "folder-music": "orange",
    "folder-videos": "red",
    "user-trash": "red",
}
_FOLDER_COLOUR = "orange"
_ROW_HEIGHT = 42
_HEADING_HEIGHT = 30
_DEVICE_COLOUR = "cyan"


def _tile(entry, is_device):
    colour = _DEVICE_COLOUR if is_device else _PLACE_COLOURS.get(entry.icon, _FOLDER_COLOUR)
    return style.tile(entry.icon + "-symbolic", entry.icon, "folder-symbolic", colour=colour)


class Sidebar(QListWidget):
    place_activated = Signal(object)  # a location

    def __init__(self, places, volumes, parent=None):
        """`places` is a list of Place, `volumes` the Volumes to show and follow."""
        super().__init__(parent)
        self.setObjectName("sidebar")
        self._places = places
        self._volumes = volumes
        self._selected = None
        self.setIconSize(QSize(style.px(28), style.px(28)))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.itemClicked.connect(self._on_clicked)
        volumes.changed.connect(self._fill)
        self._fill()

    def entries(self):
        """Every Place and Volume shown, top to bottom."""
        entries = (self.item(row).data(_EntryRole) for row in range(self.count()))
        return [entry for entry in entries if entry is not None]

    def row_of(self, name):
        """The row of the place or volume with this name, or -1."""
        return next(
            (
                row
                for row in range(self.count())
                if self.item(row).data(_EntryRole) is not None and self.item(row).text() == name
            ),
            -1,
        )

    def select(self, location):
        """Highlight the entry for `location`, or nothing when there is none."""
        self._selected = location
        self.setCurrentRow(
            next(
                (
                    row
                    for row in range(self.count())
                    if (entry := self.item(row).data(_EntryRole)) is not None
                    and locations.same(entry.location, location)
                ),
                -1,
            )
        )

    def _fill(self):
        self.clear()
        self._add_heading("Places")
        for place in self._places:
            self._add(place, is_device=False)
        devices = self._volumes.entries()
        if devices:
            self._add_heading("Devices")
        for device in devices:
            self._add(device, is_device=True)
        self.select(self._selected)

    def _add_heading(self, text):
        item = QListWidgetItem(text)
        # Not selectable, and styled as a heading through its disabled state
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        font = QFont(self.font())
        font.setPixelSize(max(8, round(font.pixelSize() * 0.85)))
        item.setFont(font)
        item.setSizeHint(QSize(0, style.px(_HEADING_HEIGHT)))
        self.addItem(item)

    def _add(self, entry, is_device):
        item = QListWidgetItem(_tile(entry, is_device), entry.name)
        item.setData(_EntryRole, entry)
        if entry.location is None:
            item.setToolTip("Not opened yet. Click to open it.")
        # Set here: the stylesheet's padding does not make these rows taller
        item.setSizeHint(QSize(0, style.px(_ROW_HEIGHT)))
        self.addItem(item)

    def _on_clicked(self, item):
        entry = item.data(_EntryRole)
        if entry is None:
            return
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
        menu = menus.menu(self)
        menu.addAction(
            style.icon("media-eject-symbolic", "media-eject"),
            entry.eject_label,
            lambda: self._volumes.eject(entry),
        )
        menu.exec(self.viewport().mapToGlobal(position))
