"""The row of tabs above the lists. Files can be dropped on a tab."""

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import QTabBar

from filehouse.ui import clipboard


class TabBar(QTabBar):
    # The tab's index, URIs, the keyboard modifiers and a global position
    dropped = Signal(int, list, object, QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMovable(True)
        self.setExpanding(False)
        self.setDrawBase(False)
        self.setElideMode(Qt.TextElideMode.ElideRight)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls() and self.tabAt(event.position().toPoint()) >= 0:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        position = event.position().toPoint()
        index = self.tabAt(position)
        uris, _cut = clipboard.read(event.mimeData())
        if index < 0 or not uris:
            event.ignore()
            return
        # As for the lists: a job does the work, and the app the files came from
        # must not remove anything itself
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()
        self.dropped.emit(index, uris, event.modifiers(), self.mapToGlobal(position))
