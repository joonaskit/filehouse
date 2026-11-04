"""The rounded groups of a toolbar: a frame with an icon button per action."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QToolButton

from filehouse.integration import style
from filehouse.ui import glass


def group(*actions):
    """A rounded group holding an icon button per action. Anything else goes into its
    layout."""
    frame = QFrame()
    frame.setObjectName("group")
    control = style.dense("control_height") + 2 * style.dense("group_padding")
    glass.light(frame, lambda: style.radius_within("large", control))
    layout = QHBoxLayout(frame)
    padding = style.px(style.dense("group_padding"))
    layout.setContentsMargins(padding, padding, padding, padding)
    layout.setSpacing(style.px(2))
    for item in actions:
        button = icon_button()
        button.setDefaultAction(item)
        layout.addWidget(button)
    return frame


def icon_button():
    """An icon button the size of those in a group, for one that is not an action's."""
    button = QToolButton()
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    button.setIconSize(QSize(style.px(18), style.px(18)))
    side = style.px(style.dense("control_height"))
    button.setFixedSize(side, side)
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return button
