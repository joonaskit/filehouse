"""Places, and below them the drives and other volumes that are plugged in."""

from PySide6.QtCore import QLocale, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QFont, QIcon, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from filehouse.core import locations
from filehouse.integration import style
from filehouse.ui import menus

_EntryRole = Qt.ItemDataRole.UserRole
# How full a drive is: a `Usage`, or nothing when that isn't known
_UsageRole = Qt.ItemDataRole.UserRole + 1
# A drive this full shows its bar in the warning colour
_NEARLY_FULL = 0.9

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
_ICON = 28
_DIVIDER_HEIGHT = 17
# Around an icon on the rail: the card's padding and the row's, on each side
_RAIL_PADDING = 2 * (8 + 6) + 2


def _tile(entry, is_device):
    colour = _DEVICE_COLOUR if is_device else _PLACE_COLOURS.get(entry.icon, _FOLDER_COLOUR)
    return style.tile(entry.icon + "-symbolic", entry.icon, "folder-symbolic", colour=colour)


class _ListDelegate(QStyledItemDelegate):
    """Draws a drive with a bar under its name for how full it is. Rows without
    a usage are left to the style."""

    def paint(self, painter, option, index):
        usage = index.data(_UsageRole)
        if usage is None:
            super().paint(painter, option, index)
            return
        row = QStyleOptionViewItem(option)
        self.initStyleOption(row, index)
        text = row.text
        # The row and its icon from the style; the name and the bar from here
        row.text = ""
        widget = row.widget
        widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, row, painter, widget)

        left = option.rect.left() + style.px(6 + _ICON + 8)
        width = option.rect.right() - style.px(10) - left
        metrics = option.fontMetrics
        gap, bar = style.px(4), style.px(3)
        top = option.rect.top() + (option.rect.height() - metrics.height() - gap - bar) // 2
        painter.save()
        painter.setFont(option.font)
        painter.setPen(option.palette.color(option.palette.ColorRole.Text))
        painter.drawText(
            QRect(left, top, width, metrics.height()),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            metrics.elidedText(text, Qt.TextElideMode.ElideRight, width),
        )
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        track = style.color("textPrimary")
        track.setAlphaF(0.15)
        painter.setBrush(track)
        y = top + metrics.height() + gap
        painter.drawRoundedRect(QRectF(left, y, width, bar), bar / 2, bar / 2)
        painter.setBrush(style.color("warning" if usage.fraction >= _NEARLY_FULL else "accent"))
        filled = max(bar, width * min(1.0, usage.fraction))
        painter.drawRoundedRect(QRectF(left, y, filled, bar), bar / 2, bar / 2)
        painter.restore()


class _RailDelegate(QStyledItemDelegate):
    """Draws a rail entry with its icon in the middle of the row. Left to the style,
    an icon without a text sits where the text's margins push it: off to one side."""

    def paint(self, painter, option, index):
        row = QStyleOptionViewItem(option)
        self.initStyleOption(row, index)
        # A copy: the option's own icon is emptied below
        icon = QIcon(row.icon)
        # The row's own look (hover, selection) from the style; the icon from here
        row.icon = QIcon()
        row.text = ""
        row.features &= ~QStyleOptionViewItem.ViewItemFeature.HasDecoration
        style_ = row.widget.style()
        style_.drawControl(QStyle.ControlElement.CE_ItemViewItem, row, painter, row.widget)
        if not icon.isNull():
            icon.paint(painter, self.icon_rect(option.rect, option.decorationSize))

    @staticmethod
    def icon_rect(row, size):
        return QRect(
            row.x() + (row.width() - size.width()) // 2,
            row.y() + (row.height() - size.height()) // 2,
            size.width(),
            size.height(),
        )


class Sidebar(QListWidget):
    place_activated = Signal(object)  # a location

    def __init__(self, places, volumes, parent=None):
        """`places` is a list of Place, `volumes` the Volumes to show and follow."""
        super().__init__(parent)
        self.setObjectName("sidebar")
        self._places = places
        self._volumes = volumes
        self._selected = None
        self._rail = False
        self._list_delegate = _ListDelegate(self)
        self.setItemDelegate(self._list_delegate)
        self._rail_delegate = _RailDelegate(self)
        self.setIconSize(QSize(style.px(_ICON), style.px(_ICON)))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.itemClicked.connect(self._on_clicked)
        volumes.changed.connect(self._fill)
        volumes.usage_changed.connect(self._show_usage)
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
                if (entry := self.item(row).data(_EntryRole)) is not None and entry.name == name
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

    @property
    def is_rail(self):
        return self._rail

    def set_rail(self, on):
        """Show the entries as icons only, for a narrow window. Their names are tooltips."""
        if on == self._rail:
            return
        self._rail = on
        self.setItemDelegate(self._rail_delegate if on else self._list_delegate)
        if on:
            self.setFixedWidth(style.px(_ICON + _RAIL_PADDING))
        else:
            self.setMinimumWidth(0)
            self.setMaximumWidth(16777215)
        self._fill()

    def _fill(self):
        self.clear()
        if not self._rail:
            self._add_heading("Places")
        for place in self._places:
            self._add(place, is_device=False)
        devices = self._volumes.entries()
        if devices:
            # On the rail a line stands in for the heading
            self._add_divider() if self._rail else self._add_heading("Devices")
        for device in devices:
            self._add(device, is_device=True)
        self.select(self._selected)
        self._show_usage()

    def _show_usage(self):
        """How full each drive is: a bar under its name, and the numbers as a tooltip."""
        locale = QLocale()
        for row in range(self.count()):
            item = self.item(row)
            entry = item.data(_EntryRole)
            usage = self._volumes.usage(entry) if hasattr(entry, "can_eject") else None
            item.setData(_UsageRole, usage)
            if usage is not None:
                free = locale.formattedDataSize(usage.free)
                tip = f"{free} free of {locale.formattedDataSize(usage.total)}"
                item.setToolTip(f"{entry.name}: {tip}" if self._rail else tip)

    def usage_shown(self, name):
        """The usage on show for the drive with this name, or None."""
        row = self.row_of(name)
        return self.item(row).data(_UsageRole) if row >= 0 else None

    def _add_divider(self):
        item = QListWidgetItem()
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setSizeHint(QSize(0, style.px(_DIVIDER_HEIGHT)))
        self.addItem(item)
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(style.px(4), 0, style.px(4), 0)
        line = QFrame()
        line.setObjectName("divider")
        line.setFixedHeight(1)
        layout.addWidget(line, 0, Qt.AlignmentFlag.AlignVCenter)
        self.setItemWidget(item, holder)

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
        item = QListWidgetItem(_tile(entry, is_device), "" if self._rail else entry.name)
        item.setData(_EntryRole, entry)
        if entry.location is None:
            item.setToolTip(
                f"{entry.name}: not opened yet. Click to open it."
                if self._rail
                else "Not opened yet. Click to open it."
            )
        elif self._rail:
            item.setToolTip(entry.name)
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
