"""One tab: a folder, or two side by side, with the one that is active."""

from PySide6.QtWidgets import QSplitter

from filehouse.integration import style


class Tab:
    def __init__(self):
        self.panes = []
        self.active = None
        self.splitter = QSplitter()
        self.splitter.setHandleWidth(style.px(8))

    @property
    def is_split(self):
        return len(self.panes) > 1
