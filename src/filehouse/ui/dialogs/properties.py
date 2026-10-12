"""Properties of one file, or the totals of several. Read only."""

from PySide6.QtCore import QDateTime, QLocale, Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QVBoxLayout

from filehouse.core import locations, properties
from filehouse.core.errors import OperationError
from filehouse.integration import style
from filehouse.qt_models.measure import Measurement


class PropertiesDialog(QDialog):
    def __init__(self, selected, parent=None):
        """`selected` is one location or more."""
        super().__init__(parent)
        self._locale = QLocale()
        self._form = QFormLayout()
        self._form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._form.setHorizontalSpacing(style.px(14))
        self._form.setVerticalSpacing(style.px(8))
        self.rows = {}  # label -> the QLabel with the value, for looking things up
        self.setMinimumWidth(style.px(420))

        measure = list(selected)
        if len(selected) == 1:
            measure = self._one(selected[0])
        else:
            self._many(selected)
        self._measurement = None
        if measure:
            self._measurement = Measurement(measure, self)
            self._measurement.progress.connect(lambda totals: self._show_totals(totals, False))
            self._measurement.finished.connect(lambda totals: self._show_totals(totals, True))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(self._form)
        layout.addStretch(1)
        layout.addWidget(buttons)

    def done(self, result):
        if self._measurement is not None:
            self._measurement.cancel()
        super().done(result)

    def _row(self, label, text):
        value = self.rows.get(label)
        if value is None:
            value = self.rows[label] = QLabel()
            value.setTextFormat(Qt.TextFormat.PlainText)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setWordWrap(True)
            self._form.addRow(label + ":", value)
        value.setText(text)

    def _one(self, location):
        """Fill in the rows for one file. Returns what still has to be measured."""
        try:
            item = properties.read(location)
        except OperationError as error:
            self.setWindowTitle("Properties")
            self._row("Name", locations.basename(location) or locations.display_name(location))
            self._row("Problem", f"Could not read it: {error.message}")
            return []
        name = "Trash" if locations.is_trash(location) else item.name
        self.setWindowTitle(f"Properties of {name}")
        self._row("Name", name)
        self._row("Type", "Link to " + item.link_target if item.link_target else item.kind)
        if item.folder:
            self._row("Location", item.folder)
        if item.original_path:
            self._row("Was in", item.original_path.rpartition("/")[0] or "/")
        # A folder's size is added up in the background and filled in when it is known
        self._row("Size", "Calculating…" if item.is_dir else self._size(item.size))
        for label, when in (
            ("Modified", item.modified),
            ("Accessed", item.accessed),
            ("Created", item.created),
        ):
            if when:
                self._row(label, self._date(when))
        if item.mode is not None:
            owner, group, others = properties.permissions(item.mode, item.is_dir)
            self._row(f"Owner ({item.owner})" if item.owner else "Owner", owner)
            self._row(f"Group ({item.group})" if item.group else "Group", group)
            self._row("Others", others)
        return [location] if item.is_dir else []

    def _many(self, selected):
        self.setWindowTitle(f"Properties of {len(selected)} items")
        self._row("Selected", f"{len(selected)} items")
        folder = locations.parent(selected[0])
        if folder is not None and all(
            locations.same(locations.parent(item), folder) for item in selected
        ):
            self._row("Location", locations.display_path(folder))
        self._row("Size", "Calculating…")

    def _show_totals(self, totals, final):
        files = "1 file" if totals.files == 1 else f"{totals.files} files"
        text = f"{self._size(totals.size)}, {files}"
        # The folder itself is not something it contains
        folders = totals.folders - (1 if "Selected" not in self.rows else 0)
        if folders > 0:
            text += ", 1 folder" if folders == 1 else f", {folders} folders"
        if not final:
            text += " so far…"
        elif totals.unreadable:
            text += ". Some folders could not be read, so it is at least this much"
        self._row("Size", text)

    def _size(self, size):
        text = self._locale.formattedDataSize(size)
        if size >= 1024:
            text += f" ({self._locale.toString(size)} bytes)"
        return text

    def _date(self, seconds):
        return self._locale.toString(
            QDateTime.fromSecsSinceEpoch(seconds), QLocale.FormatType.ShortFormat
        )
