"""Renaming several files at once, with every new name shown before anything changes."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from filehouse.core import locations
from filehouse.core.errors import OperationError
from filehouse.core.operations import rename_many
from filehouse.integration import style

REPLACE, NUMBER, UPPER, LOWER = "replace", "number", "upper", "lower"
_WAYS = (
    (REPLACE, "Find and replace"),
    (NUMBER, "A name and a number"),
    (UPPER, "Upper case"),
    (LOWER, "Lower case"),
)
_MAX_START = 999999


class RenameManyDialog(QDialog):
    """Renames `items`, which are (location, is_dir) in the order of the list, and calls
    `renamed(locations)` with where they are afterwards. It stays open, saying why,
    when they could not be renamed: none of them is then.

    Which names are taken, and the renaming, are found out and done on `background`
    (a `workers.Background`): until the answer is in, the dialog says it is checking,
    or renaming, and OK waits."""

    def __init__(self, parent, items, renamed, background):
        super().__init__(parent)
        style.translucent(self)
        self._items = list(items)
        self._renamed = renamed
        self._background = background
        self._renames = []
        # The check that is wanted, so that one asked for since is not done for nothing
        self._latest = [0]
        self.busy = False
        self.setWindowTitle(f"Rename {len(self._items)} items")
        self.setMinimumSize(style.px(520), style.px(460))

        self.way = QComboBox()
        for key, text in _WAYS:
            self.way.addItem(text, key)

        self.find = QLineEdit()
        self.replacement = QLineEdit()
        replace = QWidget()
        form = QFormLayout(replace)
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow("Find", self.find)
        form.addRow("Replace with", self.replacement)

        self.pattern = QLineEdit(self._common_start())
        self.pattern.setToolTip("“#” is where the number goes; “##” gives 01, 02, …")
        self.start = QSpinBox()
        self.start.setRange(0, _MAX_START)
        self.start.setValue(1)
        number = QWidget()
        form = QFormLayout(number)
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow("New name", self.pattern)
        form.addRow("Start at", self.start)

        self._fields = QStackedWidget()
        self._fields.addWidget(replace)
        self._fields.addWidget(number)
        # Nothing to fill in for the two cases
        self._fields.addWidget(QWidget())

        self.extension = QCheckBox("Change the extension too")

        self.preview = QTreeWidget()
        self.preview.setHeaderLabels(["Name", "New name"])
        self.preview.setRootIsDecorated(False)
        self.preview.setUniformRowHeights(True)
        self.preview.setSelectionMode(QTreeWidget.SelectionMode.NoSelection)
        self.preview.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.preview.header().setStretchLastSection(True)
        self.preview.setColumnWidth(0, style.px(240))
        for location, _is_dir in self._items:
            QTreeWidgetItem(self.preview, [locations.basename(location), ""])

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok.setText("Rename")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(self.way)
        layout.addWidget(self._fields)
        layout.addWidget(self.extension)
        layout.addWidget(self.preview, 1)
        layout.addWidget(self.status)
        layout.addWidget(self.buttons)

        self.way.currentIndexChanged.connect(self._on_way)
        for edit in (self.find, self.replacement, self.pattern):
            edit.textChanged.connect(self._update)
        self.start.valueChanged.connect(self._update)
        self.extension.toggled.connect(self._update)
        self._update()
        self.find.setFocus()

    def _common_start(self):
        """What the names begin with alike, as a first guess for the numbered name."""
        names = [locations.basename(location) for location, _is_dir in self._items]
        start = names[0] if names else ""
        for name in names[1:]:
            while start and not name.startswith(start):
                start = start[:-1]
        start = start.rstrip(" -_.(0123456789")
        return f"{start} #" if start else "#"

    def set_way(self, key):
        self.way.setCurrentIndex(self.way.findData(key))

    def _on_way(self):
        key = self.way.currentData()
        self._fields.setCurrentIndex({REPLACE: 0, NUMBER: 1}.get(key, 2))
        self._update()
        (self.pattern if key == NUMBER else self.find).setFocus()

    def _rule(self):
        key = self.way.currentData()
        if key == REPLACE:
            return rename_many.Replace(self.find.text(), self.replacement.text())
        if key == NUMBER:
            return rename_many.Number(self.pattern.text(), self.start.value())
        return rename_many.Case(upper=key == UPPER)

    @property
    def renames(self):
        """What would be done now: a `rename_many.Rename` for each item."""
        return list(self._renames)

    def _update(self, *_args):
        named = [(locations.basename(location), is_dir) for location, is_dir in self._items]
        names = rename_many.new_names(named, self._rule(), self.extension.isChecked())
        pairs = [
            (location, name) for (location, _is_dir), name in zip(self._items, names, strict=True)
        ]
        self._latest[0] += 1
        asked, latest = self._latest[0], self._latest

        def check():
            # Typed over already: what this would find is not wanted
            return rename_many.check(pairs) if latest[0] == asked else None

        self.busy = True
        self._ok.setEnabled(False)
        self._background.call(check, lambda renames: self._show(asked, renames))

    def _show(self, asked, renames):
        if asked != self._latest[0] or self._renamed is None:
            return
        self.busy = False
        self._renames = renames
        danger = style.color("danger")
        for row, rename in enumerate(self._renames):
            item = self.preview.topLevelItem(row)
            item.setText(1, rename.name)
            item.setToolTip(1, rename.problem.capitalize())
            item.setData(1, Qt.ItemDataRole.ForegroundRole, danger if rename.problem else None)
            # A name that stays is there to be read, and no more
            item.setDisabled(not rename.changes and not rename.problem)
        blocked = sum(bool(rename.problem) for rename in self._renames)
        changing = sum(rename.changes for rename in self._renames)
        if blocked:
            first = next(rename for rename in self._renames if rename.problem)
            more = f", and {blocked - 1} more" if blocked > 1 else ""
            self._say(f"“{first.name}”: {first.problem}{more}. Nothing is renamed.", True)
        elif not changing:
            self._say("No name changes.", False)
        else:
            self._say(
                "1 item gets a new name." if changing == 1 else f"{changing} items get new names.",
                False,
            )
        self._ok.setEnabled(not blocked and changing > 0)

    def _say(self, text, problem):
        self.status.setStyleSheet(f"color: {style.color('danger').name()};" if problem else "")
        self.status.setText(text)

    def done(self, result):
        # `renamed` belongs to the window that opened this; don't hold on to it past closing
        self._renamed = None
        super().done(result)

    def accept(self):
        if self._renamed is None or self.busy or not self._ok.isEnabled():
            return
        # Called with what was done, also when the dialog was closed meanwhile
        renamed, renames = self._renamed, self._renames
        self._set_renaming(True)

        def finished(now):
            renamed(now)
            if self._renamed is not None:
                self._set_renaming(False)
                super(RenameManyDialog, self).accept()

        def failed(error):
            if self._renamed is None:
                return
            self._set_renaming(False)
            if isinstance(error, OperationError):
                self._say(
                    f"Could not rename “{error.name}”: {error.message}. Nothing is renamed.", True
                )
            else:
                self._say(f"Could not rename: internal error: {error}.", True)

        self._background.call(lambda: rename_many.rename_all(renames), finished, failed)

    def _set_renaming(self, renaming):
        self.busy = renaming
        self._ok.setEnabled(not renaming)
        for field in (self.way, self.find, self.replacement, self.pattern, self.start):
            field.setEnabled(not renaming)
        self.extension.setEnabled(not renaming)
        if renaming:
            self._say("Renaming…", False)
