"""The row of tabs above the lists. Files can be dropped on a tab."""

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtWidgets import QTabBar

from filehouse.integration import style
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

    def tabLayoutChange(self):
        self._place_close_buttons()

    def paintEvent(self, event):
        # The style moves them again when a name changes: no later than the next paint
        self._place_close_buttons()
        super().paintEvent(event)

    def _place_close_buttons(self):
        # The style puts the button by the tab's whole rectangle, which includes the
        # gap that is not drawn: put it in the middle of the tab as it looks instead
        gap = style.px(style.TAB_GAP)
        for index in range(self.count()):
            button = self.tabButton(index, QTabBar.ButtonPosition.RightSide)
            if button is None:
                continue
            rect = self.tabRect(index)
            button.move(
                rect.right() + 1 - gap - style.px(style.TAB_CLOSE_INSET) - button.width(),
                rect.top() + (rect.height() - gap - button.height()) // 2,
            )

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
