"""Places. Mounted volumes come later."""

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QListWidget, QListWidgetItem

from filehouse.integration import style


class Sidebar(QListWidget):
    place_activated = Signal(object)  # a location

    def __init__(self, places, parent=None):
        super().__init__(parent)
        self._places = places
        self.setIconSize(QSize(style.px(18), style.px(18)))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for place in places:
            self.addItem(QListWidgetItem(QIcon.fromTheme(place.icon), place.name))
        self.itemClicked.connect(self._on_clicked)

    def select(self, index):
        """Highlight the place at `index`; -1 for none."""
        self.setCurrentRow(index)

    def _on_clicked(self, item):
        self.place_activated.emit(self._places[self.row(item)].location)
