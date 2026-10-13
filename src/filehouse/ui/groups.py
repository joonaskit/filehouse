"""The rounded groups of a toolbar: a frame with an icon button per action."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QToolButton

from filehouse.integration import style


def group(*actions):
    """A rounded group holding an icon button per action. Anything else goes into its
    layout."""
    frame = QFrame()
    frame.setObjectName("group")
    layout = QHBoxLayout(frame)
    padding = style.px(style.dense("group_padding"))
    layout.setContentsMargins(padding, padding, padding, padding)
    layout.setSpacing(style.px(2))
    for item in actions:
        button = QToolButton()
        button.setDefaultAction(item)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setIconSize(QSize(style.px(18), style.px(18)))
        side = style.px(style.dense("control_height"))
        button.setFixedSize(side, side)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout.addWidget(button)
    return frame
