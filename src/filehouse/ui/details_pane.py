"""The pane beside the list: what is selected, or the open folder when nothing is.

In a narrow window the same details are a sheet at the bottom: the compact form."""

import getpass

from PySide6.QtCore import QLocale, QSize, Qt, Signal
from PySide6.QtGui import QFont, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from filehouse.core import locations
from filehouse.core.errors import OperationError
from filehouse.core.listing import mode_text
from filehouse.integration import style
from filehouse.qt_models import dates
from filehouse.qt_models.details import Reading
from filehouse.qt_models.icons import icon_for_content_type
from filehouse.qt_models.measure import Measurement

_ICON = 64
_COMPACT_ICON = 40
_NAME_FONT_PX = 16
# A longer name is cut in the middle. The whole of it is in the tooltip
_NAME_LENGTH = 80
_ROWS = ("Modified", "Size", "Contains", "Owner")
_WHO = ("Owner", "Group", "Others")
_SEVERAL = "inode/x-several"


def _breakable(text):
    """`text` with a place to break after every character: a label only wraps
    between words, and a file name is often one long word."""
    return "​".join(text)


class _Dot(QWidget):
    """One permission: filled in the success colour when it is given."""

    def __init__(self):
        super().__init__()
        self.on = False
        side = style.px(14)
        self.setFixedSize(side, side)

    def set_on(self, on, tip):
        self.on = on
        self.setToolTip(tip)
        self.setAccessibleName(tip)
        self.update()

    def paintEvent(self, _event):
        colour = style.color("success" if self.on else "textPrimary")
        if not self.on:
            colour.setAlphaF(0.15)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colour)
        painter.drawEllipse(self.rect())


class DetailsPane(QFrame):
    open_requested = Signal()  # the Open button, for the one item shown
    close_requested = Signal()  # the close button of the compact form

    def __init__(self, compact=False, parent=None):
        """`compact` is the form for a sheet: the icon beside the name, a button
        to close it, and no Open button."""
        super().__init__(parent)
        self.setObjectName("card")
        self.setMinimumWidth(style.px(220))
        self._compact = compact
        self._locale = QLocale()
        self._shown = None  # what show_items was last asked for
        self._reading = None
        self._measurement = None
        self._is_folder = False
        self._icon_type = None  # the content type of the icon on show
        self.close_button = None

        self._icon = QLabel()
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name = QLabel()
        font = QFont(self.font())
        font.setPixelSize(style.font_px(_NAME_FONT_PX))
        font.setBold(True)
        self.name.setFont(font)
        self.kind = self._secondary()
        # Values and the names of the permission rows stand out from their labels
        strong = QFont(self.font())
        strong.setWeight(QFont.Weight.DemiBold)
        for label in (self.name, self.kind):
            if not compact:
                label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.PlainText)
            # Narrower than its text is fine: it wraps
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        # label -> the QLabel with the value, for looking things up
        self.rows = {}
        self._row_labels = {}
        rows = QGridLayout()
        rows.setVerticalSpacing(style.px(10))
        rows.setColumnStretch(1, 1)
        for index, text in enumerate(_ROWS):
            label = self._row_labels[text] = self._secondary(text)
            value = self.rows[text] = QLabel()
            value.setFont(strong)
            value.setTextFormat(Qt.TextFormat.PlainText)
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            rows.addWidget(label, index, 0)
            rows.addWidget(value, index, 1)

        # Read only: changing permissions comes with step 6g
        self._permissions = QWidget()
        grid = QGridLayout(self._permissions)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setVerticalSpacing(style.px(14))
        grid.setColumnStretch(0, 1)
        grid.addWidget(self._secondary("Permissions"), 0, 0)
        self.mode = QLabel()
        self.mode.setFont(style.fixed_font())
        grid.addWidget(self.mode, 0, 1, 1, 3, Qt.AlignmentFlag.AlignRight)
        self._can = [self._secondary(text) for text in ("Read", "Write", "Run")]
        for column, label in enumerate(self._can, start=1):
            grid.addWidget(label, 1, column, Qt.AlignmentFlag.AlignCenter)
            grid.setColumnMinimumWidth(column, style.px(40))
        self.dots = []  # a row of three per owner, group and others
        for row, who in enumerate(_WHO, start=2):
            name = QLabel(who)
            name.setFont(strong)
            grid.addWidget(name, row, 0)
            dots = [_Dot() for _ in range(3)]
            for column, dot in enumerate(dots, start=1):
                grid.addWidget(dot, row, column, Qt.AlignmentFlag.AlignCenter)
            self.dots.append(dots)

        self.open_button = QPushButton("Open")
        # The height its corners are made for: see style.radius_within
        self.open_button.setFixedHeight(style.px(style.BUTTON_HEIGHT))
        self.open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.open_button.clicked.connect(self.open_requested)

        self._first_divider = self._divider()
        self._second_divider = self._divider()
        layout = QVBoxLayout(self)
        margin = style.px(16)
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.setSpacing(style.px(12))
        names = QVBoxLayout()
        names.setSpacing(style.px(2))
        names.addWidget(self.name)
        names.addWidget(self.kind)
        if compact:
            self.close_button = QToolButton()
            self.close_button.setIcon(style.icon("window-close-symbolic", "window-close"))
            self.close_button.setToolTip("Close")
            self.close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.close_button.clicked.connect(self.close_requested)
            header = QHBoxLayout()
            header.setSpacing(style.px(12))
            header.addWidget(self._icon)
            header.addLayout(names, 1)
            header.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignTop)
            layout.addLayout(header)
        else:
            layout.addWidget(self._icon)
            layout.addLayout(names)
        layout.addWidget(self._first_divider)
        layout.addLayout(rows)
        layout.addWidget(self._second_divider)
        layout.addWidget(self._permissions)
        if compact:
            self.open_button.setParent(self)
        else:
            layout.addStretch(1)
            layout.addWidget(self.open_button)
        self._show_nothing()

    @staticmethod
    def _secondary(text=""):
        label = QLabel(text)
        label.setObjectName("secondary")
        return label

    @staticmethod
    def _divider():
        line = QFrame()
        line.setObjectName("divider")
        line.setFixedHeight(1)
        return line

    # What to show

    def show_items(self, folder, infos, item_count=None):
        """Show the entries `infos` (FileInfo) of `folder`, or the folder itself when
        there are none. `item_count` is how many entries the folder is showing."""
        asked = (locations.uri(folder), tuple(infos), None if infos else item_count)
        if asked == self._shown:
            return
        self._shown = asked
        self._stop()
        if len(infos) > 1:
            self._show_several([locations.child(folder, info.name) for info in infos])
            return
        self._is_folder = not infos
        target = locations.child(folder, infos[0].name) if infos else folder
        # What is on show stays until the answer is in, so the pane doesn't blink
        self._reading = Reading(target, self)
        self._reading.done.connect(
            lambda result, reading=self._reading: self._on_read(reading, target, result, item_count)
        )

    def _stop(self):
        if self._reading is not None:
            self._reading.deleteLater()
            self._reading = None
        if self._measurement is not None:
            self._measurement.cancel()
            self._measurement.deleteLater()
            self._measurement = None

    def _on_read(self, reading, target, result, item_count):
        if reading is not self._reading:
            return
        if isinstance(result, OperationError):
            self._show_nothing()
            self._set_name(locations.basename(target) or locations.display_name(target))
            self.kind.setText(f"Could not read it: {result.message}")
            return
        item = result
        self._set_icon(item.content_type)
        self._set_name(locations.display_name(target) if self._is_folder else item.name)
        self.kind.setText("Link to " + item.link_target if item.link_target else item.kind)
        self._set_row("Modified", dates.friendly(item.modified, self._locale))
        self._set_row("Contains", None)
        if self._is_folder:
            # The open folder: what the list shows. Adding up all that is under it
            # would walk the whole drive for "/"
            count = item_count or 0
            self._row_labels["Size"].setText("Items")
            self._set_row("Size", None if item_count is None else str(count))
        else:
            self._row_labels["Size"].setText("Size")
            self._set_row(
                "Size", "Calculating…" if item.is_dir else self._locale.formattedDataSize(item.size)
            )
        owner = "you" if item.owner == getpass.getuser() else item.owner
        self._set_row("Owner", " · ".join(part for part in (owner, item.group) if part) or None)
        self._first_divider.show()
        self._set_mode(item.mode, item.is_dir)
        self.open_button.setVisible(not self._is_folder and not self._compact)
        if item.is_dir and not self._is_folder:
            self._measure([target], inside_one=True)

    def _show_several(self, selected):
        self._show_nothing()
        self._set_icon(_SEVERAL)
        self._set_name(f"{len(selected)} items")
        self.kind.setText("Selected")
        self._row_labels["Size"].setText("Size")
        self._set_row("Size", "Calculating…")
        self._first_divider.show()
        self._measure(selected, inside_one=False)

    def _show_nothing(self):
        self._icon.clear()
        self._icon_type = None
        self.name.clear()
        self.name.setToolTip("")
        self.kind.clear()
        for label in _ROWS:
            self._set_row(label, None)
        self._first_divider.hide()
        self._set_mode(None, False)
        self.open_button.hide()

    # Folders are added up in the background

    def _measure(self, roots, inside_one):
        measurement = self._measurement = Measurement(roots, self)
        measurement.progress.connect(
            lambda totals: self._show_totals(measurement, totals, inside_one, final=False)
        )
        measurement.finished.connect(
            lambda totals: self._show_totals(measurement, totals, inside_one, final=True)
        )

    def _show_totals(self, measurement, totals, inside_one, final):
        if measurement is not self._measurement:
            return
        size = self._locale.formattedDataSize(totals.size)
        if not final:
            size += "…"
        elif totals.unreadable:
            size = "At least " + size
            self.rows["Size"].setToolTip("Some folders could not be read.")
        self._set_row("Size", size, keep_tip=True)
        # A folder is not something it contains itself
        folders = totals.folders - (1 if inside_one else 0)
        parts = ["1 file" if totals.files == 1 else f"{totals.files} files"]
        if folders > 0:
            parts.append("1 folder" if folders == 1 else f"{folders} folders")
        self._row_labels["Contains"].setText("Contains" if inside_one else "In all")
        self._set_row("Contains", ", ".join(parts))

    # The parts

    def retheme(self):
        """The icons again, in the colours of the theme now in use."""
        if self.close_button is not None:
            self.close_button.setIcon(style.icon("window-close-symbolic", "window-close"))
        if self._icon_type is not None:
            self._set_icon(self._icon_type)

    def _set_icon(self, content_type):
        self._icon_type = content_type
        side = style.px(_COMPACT_ICON if self._compact else _ICON)
        self._icon.setPixmap(icon_for_content_type(content_type).pixmap(QSize(side, side)))

    def _set_name(self, name):
        shown = name
        if len(name) > _NAME_LENGTH:
            half = _NAME_LENGTH // 2
            shown = f"{name[:half]}…{name[-half:]}"
        self.name.setText(_breakable(shown))
        self.name.setToolTip(name)

    def _set_row(self, label, text, keep_tip=False):
        """Fill in a row, or hide it when there is nothing to say."""
        value = self.rows[label]
        value.setText(text or "")
        if not keep_tip:
            value.setToolTip("")
        for widget in (value, self._row_labels[label]):
            widget.setVisible(text is not None)

    def _set_mode(self, mode, is_dir):
        self._permissions.setVisible(mode is not None)
        self._second_divider.setVisible(mode is not None)
        if mode is None:
            return
        octal = f"{mode & 0o777:03o}"
        # The sheet has the room to spell it out as well
        self.mode.setText(f"{mode_text(mode & 0o777)} · {octal}" if self._compact else octal)
        words = ("read", "write", "open" if is_dir else "run")
        self._can[2].setText(words[2].capitalize())
        for dots, who, shift in zip(self.dots, _WHO, (6, 3, 0), strict=True):
            for dot, word, bit in zip(dots, words, (4, 2, 1), strict=True):
                on = bool((mode >> shift) & bit)
                dot.set_on(on, f"{who} may {word} it" if on else f"{who} may not {word} it")

    def permissions_shown(self):
        """What the dots say, as the three texts `properties.permissions` gives."""
        words = [label.text().lower() for label in self._can]
        result = []
        for dots in self.dots:
            allowed = [word for dot, word in zip(dots, words, strict=True) if dot.on]
            result.append(", ".join(allowed).capitalize() if allowed else "Nothing")
        return tuple(result)
