"""Places, the favourites, and below them the drives and other volumes that are plugged in.

Each of the three is a group that folds shut when its heading is clicked."""

from PySide6.QtCore import QLocale, QMetaMethod, QMimeData, QPoint, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QFont, QFontInfo, QIcon, QPainter, QPalette
from PySide6.QtWidgets import (
    QApplication,
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
from filehouse.qt_models.favourites import Favourite
from filehouse.qt_models.workers import Background
from filehouse.ui import clipboard, glass, menus, settings
from filehouse.ui.folder_view import history_step

_EntryRole = Qt.ItemDataRole.UserRole
# How full a drive is: a `Usage`, or nothing when that isn't known
_UsageRole = Qt.ItemDataRole.UserRole + 1
# Whether an entry is a drive, which has its own colour
_DeviceRole = Qt.ItemDataRole.UserRole + 2
# On a heading: which group it opens and closes. On a favourite: the Favourite
_GroupRole = Qt.ItemDataRole.UserRole + 3
_FavouriteRole = Qt.ItemDataRole.UserRole + 4
# On the line that says what an empty group is for
_HintRole = Qt.ItemDataRole.UserRole + 5
_PLACES, _FAVOURITES, _DEVICES = "places", "favourites", "devices"
# What a dragged favourite carries: its ID, and nothing a file manager could take for a
# file, so that dropping it on a folder cannot move the folder
FAVOURITE_MIME = "application/x-filehouse-favourite"
_DROP_LINE = 2
# A favourite's rows are cut in this many parts, and the first and the last are the edges
# where a folder dropped on it is added next to it, not copied into it
_EDGES = 4
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
    "document-open-recent": "cyan",
}
_FOLDER_COLOUR = "orange"
_DEVICE_COLOUR = "cyan"
_DIVIDER_HEIGHT = 17
# Around an icon on the rail: the card's padding and the row's, on each side
_RAIL_PADDING = 2 * (8 + 6) + 2


def _tile(entry, is_device):
    colour = _DEVICE_COLOUR if is_device else _PLACE_COLOURS.get(entry.icon, _FOLDER_COLOUR)
    # In the one size the list shows it at
    return style.tile(
        entry.icon + "-symbolic",
        entry.icon,
        "folder-symbolic",
        colour=colour,
        side=style.dense("sidebar_icon"),
    )


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

        left = option.rect.left() + style.px(6 + style.dense("sidebar_icon") + 8)
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
    new_tab_requested = Signal(object)  # a location
    # The menu of a favourite: the Favourite, and a global position
    favourite_menu_requested = Signal(object, QPoint)
    place_menu_requested = Signal(object, QPoint)  # an entry (a place or a device)
    # Files were dropped on an entry: its location, the URIs, the keyboard modifiers and a
    # global position
    files_dropped = Signal(object, list, object, QPoint)
    # Folders were dropped on the favourites, on none of them: the URIs, and the place they
    # are to take in the list (0 is the first, -1 the end)
    folders_dropped = Signal(list, int)

    def __init__(self, places, volumes, favourites=None, parent=None):
        """`places` is a list of Place, `volumes` the Volumes to show and follow, and
        `favourites` the Favourites to show in a group of their own, if any."""
        super().__init__(parent)
        self.setObjectName("sidebar")
        glass.light(self)
        # The app's own font, and set here: left alone, the desktop gives a list a font of
        # its own (a size in points) when the window is first shown, and what is read from
        # it before and after, such as the size of a heading, is not the same
        self.setFont(QApplication.font())
        self._places = places
        self._volumes = volumes
        self._favourites = favourites
        self._selected = None
        self._rail = False
        self._list_delegate = _ListDelegate(self)
        self.setItemDelegate(self._list_delegate)
        self._rail_delegate = _RailDelegate(self)
        self.setIconSize(
            QSize(style.px(style.dense("sidebar_icon")), style.px(style.dense("sidebar_icon")))
        )
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.itemClicked.connect(self._on_clicked)
        self.setAcceptDrops(True)
        # Only the favourites can be picked up: to put them in another order
        self.setDragEnabled(True)
        self.setDragDropMode(QListWidget.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._drop_line = None  # where a dragged favourite would go: a y in the viewport
        # Whether what is dragged over it is folders only, which can be made favourites:
        # None until that is known. It is asked on a worker, `_drag` counting the drags
        self._dragging_folders = False
        self._drag = 0
        self._background = Background(self, in_turn=False)
        volumes.changed.connect(self._fill)
        volumes.usage_changed.connect(self._show_usage)
        if favourites is not None:
            favourites.changed.connect(self._fill)
        self._fill()

    def set_places(self, places):
        """Show these places instead of the ones it was given."""

        def shown(entries):
            return [(place.name, locations.uri(place.location)) for place in entries]

        if shown(places) != shown(self._places):
            self._places = places
            self._fill()

    @property
    def busy(self):
        """Whether it is still being asked what is dragged over it."""
        return self._background.busy

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
            self.setFixedWidth(style.px(style.dense("sidebar_icon") + _RAIL_PADDING))
        else:
            self.setMinimumWidth(0)
            self.setMaximumWidth(16777215)
        self._fill()

    def _fill(self):
        self.clear()
        collapsed = set(settings.collapsed_groups())
        favourites = self._favourites.items if self._favourites is not None else []
        devices = self._volumes.entries()
        groups = [(_PLACES, "Places", self._places, False)]
        if self._favourites is not None:
            groups.append((_FAVOURITES, "Favourites", favourites, False))
        if devices:
            groups.append((_DEVICES, "Devices", devices, True))
        for index, (key, title, entries, is_device) in enumerate(groups):
            if self._rail:
                # A line stands in for a heading, and nothing is folded: there is no heading
                # to unfold it by. Not for the first group, nor one with nothing in it
                if entries and index:
                    self._add_divider()
                shown = entries
            else:
                self._add_heading(key, title, key in collapsed)
                shown = [] if key in collapsed else entries
            for entry in shown:
                self._add_entry(entry, is_device)
            if key == _FAVOURITES and not entries and not self._rail and key not in collapsed:
                self._add_hint()
        self.select(self._selected)
        self._show_usage()

    def _add_entry(self, entry, is_device):
        if isinstance(entry, Favourite):
            # A favourite: shown as a place, and kept to be changed
            self._add(locations.Place(entry.title, entry.icon, entry.location), False, entry)
        else:
            self._add(entry, is_device)

    def _add_hint(self):
        available = self._favourites.available
        item = QListWidgetItem("Drag a folder here" if available else "Not available yet")
        item.setToolTip(
            "Or press Ctrl+D in a folder"
            if available
            else "Open Dolphin once: the favourites are kept in its list of places"
        )
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setData(_HintRole, True)
        font = QFont(self.font())
        font.setPixelSize(max(8, round(self._text_px() * 0.92)))
        item.setFont(font)
        item.setSizeHint(QSize(0, style.px(style.dense("sidebar_heading"))))
        self.addItem(item)

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

    def _text_px(self):
        """The size of the text of the entries, in pixels. A font that the desktop gave in
        points has no pixel size of its own (it says -1), so it is asked of what is drawn."""
        return QFontInfo(self.font()).pixelSize()

    def _add_heading(self, key, text, collapsed):
        item = QListWidgetItem(("▸ " if collapsed else "▾ ") + text)
        # Not selectable, but it takes a click: it folds the group
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        item.setData(_GroupRole, key)
        item.setForeground(QBrush(self.palette().color(QPalette.ColorRole.PlaceholderText)))
        item.setToolTip("Click to show or hide")
        font = QFont(self.font())
        font.setWeight(QFont.Weight.DemiBold)
        font.setPixelSize(self._text_px())
        item.setFont(font)
        item.setSizeHint(QSize(0, style.px(style.dense("sidebar_heading"))))
        self.addItem(item)

    def _add(self, entry, is_device, favourite=None):
        item = QListWidgetItem(_tile(entry, is_device), "" if self._rail else entry.name)
        item.setData(_EntryRole, entry)
        item.setData(_DeviceRole, is_device)
        if favourite is not None:
            item.setData(_FavouriteRole, favourite)
            item.setToolTip(favourite.location.get_parse_name())
        else:
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
        if entry.location is None:
            item.setToolTip(
                f"{entry.name}: not opened yet. Click to open it."
                if self._rail
                else "Not opened yet. Click to open it."
            )
        elif self._rail:
            item.setToolTip(entry.name)
        # Set here: the stylesheet's padding does not make these rows taller
        item.setSizeHint(QSize(0, style.px(style.dense("sidebar_row"))))
        self.addItem(item)

    def retheme(self):
        """The icons and headings again, in the colours of the theme now in use."""
        self._fill()

    def _on_clicked(self, item):
        group = item.data(_GroupRole)
        if group is not None:
            settings.set_group_collapsed(group, group not in settings.collapsed_groups())
            self._fill()
            return
        entry = item.data(_EntryRole)
        if entry is None:
            return
        if entry.location is not None:
            self.place_activated.emit(entry.location)
        else:
            # A drive that is plugged in but not mounted: mount it, then go there
            self._volumes.mount(entry, self.place_activated.emit)

    def mousePressEvent(self, event):
        if history_step(event):
            # The mouse's back or forward button: the window's, not a click on a place
            event.ignore()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if history_step(event):
            event.ignore()
            return
        super().mouseDoubleClickEvent(event)

    def mouseReleaseEvent(self, event):
        # A middle click opens a place in a tab
        if event.button() == Qt.MouseButton.MiddleButton:
            item = self.itemAt(event.position().toPoint())
            entry = item.data(_EntryRole) if item is not None else None
            if entry is not None and entry.location is not None:
                self.new_tab_requested.emit(entry.location)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # A favourite can be dragged to another place in its group

    def mimeTypes(self):
        return [FAVOURITE_MIME]

    def mimeData(self, items):
        favourite = items[0].data(_FavouriteRole) if items else None
        if favourite is None:
            return None
        data = QMimeData()
        data.setData(FAVOURITE_MIME, favourite.id.encode())
        return data

    def _favourite_rows(self):
        return [r for r in range(self.count()) if self.item(r).data(_FavouriteRole) is not None]

    def _insertion(self, position):
        """Where a favourite dropped at `position` would go: its index among the favourites
        (the one it would stand before) and the y of the gap, or None outside the group."""
        rows = self._favourite_rows()
        item = self.itemAt(position)
        if item is None:
            return None
        rect = self.visualItemRect(item)
        if item.data(_FavouriteRole) is not None:
            before = position.y() < rect.center().y()
            index = rows.index(self.row(item)) + (0 if before else 1)
            return index, rect.top() if before else rect.bottom()
        if item.data(_GroupRole) == _FAVOURITES:
            return 0, rect.bottom()
        return None

    def _dragged_favourite(self, mime):
        if not mime.hasFormat(FAVOURITE_MIME) or self._favourites is None:
            return None
        favourite_id = bytes(mime.data(FAVOURITE_MIME)).decode()
        return next((f for f in self._favourites.items if f.id == favourite_id), None)

    def _show_drop_line(self, y):
        if y != self._drop_line:
            self._drop_line = y
            self.viewport().update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._drop_line is not None:
            painter = QPainter(self.viewport())
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self.palette().color(QPalette.ColorRole.Highlight))
            painter.drawRoundedRect(
                QRectF(
                    style.px(8),
                    self._drop_line - style.px(_DROP_LINE) / 2,
                    self.viewport().width() - 2 * style.px(8),
                    style.px(_DROP_LINE),
                ),
                style.px(_DROP_LINE) / 2,
                style.px(_DROP_LINE) / 2,
            )

    def dragLeaveEvent(self, event):
        self._drag += 1
        self._dragging_folders = False
        self._show_drop_line(None)
        super().dragLeaveEvent(event)

    # Files dropped on an entry go into it, and a folder dropped among the favourites is added

    def _look_at_drag(self, mime):
        """Find out whether what comes in is folders only, on a worker: a folder that does
        not answer holds up nothing. Until it is known it is not."""
        self._drag += 1
        drag = self._drag
        self._dragging_folders = None
        uris, _cut = clipboard.read(mime)

        def only_folders():
            return bool(uris) and all(locations.is_folder(locations.from_uri(uri)) for uri in uris)

        def known(folders):
            if drag == self._drag:
                self._dragging_folders = folders

        self._background.call(only_folders, known)

    def _folder_insertion(self, position, folders=None):
        """Where folders dropped at `position` would be added among the favourites, as for
        a favourite that is moved: the edges of a favourite are between it and the next,
        and the middle of it is a drop into it. None where they would not be added, or when
        what is dragged is not folders only (`folders`, by default as far as is known)."""
        if folders is None:
            folders = bool(self._dragging_folders)
        if not folders or self._favourites is None or not self._favourites.available:
            return None
        item = self.itemAt(position)
        if item is None or item.data(_FavouriteRole) is None:
            return None
        rect = self.visualItemRect(item)
        edge = rect.height() // _EDGES
        if position.y() < rect.top() + edge:
            before = True
        elif position.y() > rect.bottom() - edge:
            before = False
        else:
            return None
        index = self._favourite_rows().index(self.row(item)) + (0 if before else 1)
        return index, rect.top() if before else rect.bottom()

    def dragEnterEvent(self, event):
        mime = event.mimeData()
        if mime.hasUrls():
            self._look_at_drag(mime)
        else:
            self._drag += 1
            self._dragging_folders = False
        if mime.hasUrls() or self._dragged_favourite(mime):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        mime = event.mimeData()
        if self._dragged_favourite(mime) is not None:
            target = self._insertion(event.position().toPoint())
            self._show_drop_line(target[1] if target else None)
            if target is not None:
                event.setDropAction(Qt.DropAction.MoveAction)
                event.accept()
            else:
                event.ignore()
        elif mime.hasUrls():
            target = self._folder_insertion(event.position().toPoint())
            self._show_drop_line(target[1] if target else None)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        self._show_drop_line(None)
        dragged = self._dragged_favourite(event.mimeData())
        if dragged is not None:
            self._drop_favourite(event, dragged)
            return
        uris, _cut = clipboard.read(event.mimeData())
        if not uris:
            event.ignore()
            return
        position = event.position().toPoint()
        dragging_folders = self._dragging_folders
        self._drag += 1
        self._dragging_folders = False
        if dragging_folders is None and self._folder_insertion(position, True) is not None:
            # Dropped at the edge of a favourite before it is known what this is: added
            # if it is folders, and put into the favourite if not. Neither, rather than
            # the wrong one
            event.ignore()
            return
        between = self._folder_insertion(position, bool(dragging_folders))
        if between is not None:
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            self.folders_dropped.emit(uris, between[0])
            return
        item = self.itemAt(position)
        entry = item.data(_EntryRole) if item is not None else None
        on_favourites = item is not None and (
            item.data(_GroupRole) == _FAVOURITES or item.data(_HintRole)
        )
        if entry is not None and entry.location is not None:
            # As for the lists: a job does the work, and the app the files came from must
            # not remove anything itself
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            self.files_dropped.emit(
                entry.location, uris, event.modifiers(), self.viewport().mapToGlobal(position)
            )
        elif on_favourites and self._favourites is not None:
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            self.folders_dropped.emit(uris, -1)
        else:
            event.ignore()

    def _drop_favourite(self, event, dragged):
        target = self._insertion(event.position().toPoint())
        if target is None:
            event.ignore()
            return
        before = target[0]
        items = self._favourites.items
        current = next((i for i, f in enumerate(items) if f.id == dragged.id), None)
        if current is not None:
            # The index asked for counts the one being moved; the list without it is shorter
            index = before - 1 if before > current else before
            if index != current:
                self._favourites.move_to(dragged, index)
        # Not a move: a list that was dragged from takes a move to mean that what was dragged
        # is to be removed from it, and it is the favourites that are changed, not the rows
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def eject(self, entry):
        self._volumes.eject(entry)

    def _on_context_menu(self, position):
        item = self.itemAt(position)
        favourite = item.data(_FavouriteRole) if item is not None else None
        if favourite is not None:
            self.favourite_menu_requested.emit(favourite, self.viewport().mapToGlobal(position))
            return
        entry = item.data(_EntryRole) if item is not None else None
        if entry is None:
            return
        if self.isSignalConnected(QMetaMethod.fromSignal(self.place_menu_requested)):
            if entry.location is not None:
                self.place_menu_requested.emit(entry, self.viewport().mapToGlobal(position))
            return
        if not getattr(entry, "can_eject", False):
            return
        menu = menus.menu(self)
        menu.addAction(
            style.icon("media-eject-symbolic", "media-eject", menu=True),
            entry.eject_label,
            lambda: self._volumes.eject(entry),
        )
        menu.exec(self.viewport().mapToGlobal(position))
