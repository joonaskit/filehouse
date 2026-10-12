"""A row of the user's favourite folders under the toolbar, as the bookmarks bar of a browser.

A click goes there, a middle click opens it in a tab, a menu changes it, and files
dropped on one are put in the folder. A folder dropped on the empty part is added.
"""

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QToolBar

from filehouse.integration import style
from filehouse.ui import clipboard

_ICON = 20


class FavouritesBar(QToolBar):
    open_requested = Signal(object)  # Favourite
    new_tab_requested = Signal(object)
    context_requested = Signal(object, QPoint)  # Favourite, a global position
    # Files were dropped on a favourite: it, the URIs, the keyboard modifiers, a global position
    dropped = Signal(object, list, object, QPoint)
    # URIs were dropped on the empty part of the bar
    folders_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__("Favourites", parent)
        self.setObjectName("favourites")
        self.setMovable(False)
        self.setFloatable(False)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setIconSize(QSize(style.px(_ICON), style.px(_ICON)))
        self.setAcceptDrops(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self._favourites = {}  # QAction -> Favourite

    @property
    def count(self):
        return len(self._favourites)

    def set_items(self, favourites):
        for action in list(self._favourites):
            self.removeAction(action)
            action.deleteLater()
        self._favourites = {}
        for favourite in favourites:
            action = QAction(self._icon(favourite), favourite.title, self)
            action.setToolTip(favourite.location.get_parse_name())
            action.triggered.connect(
                lambda _checked=False, f=favourite: self.open_requested.emit(f)
            )
            self.addAction(action)
            self._favourites[action] = favourite
            # For the middle click, which a QAction knows nothing of
            self.widgetForAction(action).installEventFilter(self)

    def retheme(self):
        """The icons again, in the colours of the theme now in use."""
        for action, favourite in self._favourites.items():
            action.setIcon(self._icon(favourite))

    @staticmethod
    def _icon(favourite):
        icon = favourite.icon
        return style.tile(icon + "-symbolic", icon, "folder-symbolic", colour="orange")

    def favourite_at(self, position):
        action = self.actionAt(position)
        return self._favourites.get(action)

    # Mouse

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseButtonRelease and (
            event.button() == Qt.MouseButton.MiddleButton
        ):
            for action, favourite in self._favourites.items():
                if self.widgetForAction(action) is watched:
                    self.new_tab_requested.emit(favourite)
                    return True
        return super().eventFilter(watched, event)

    def _on_context_menu(self, position):
        favourite = self.favourite_at(position)
        if favourite is not None:
            self.context_requested.emit(favourite, self.mapToGlobal(position))

    # Drops

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        uris, _cut = clipboard.read(event.mimeData())
        if not uris:
            event.ignore()
            return
        position = event.position().toPoint()
        favourite = self.favourite_at(position)
        # As for the lists: a job does the work, and the app the files came from must
        # not remove anything itself
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        if favourite is not None:
            self.dropped.emit(favourite, uris, event.modifiers(), self.mapToGlobal(position))
        else:
            self.folders_dropped.emit(uris)
