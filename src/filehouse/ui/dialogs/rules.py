"""The rules of a folder (#87): the ordered list, the editor of one rule, and a preview
of what the rules would do to the folder now. And "Run rules now", which shows the same
preview before anything is done.

A rule is saved only when it can work (`automations.check_rule`), and only after its
preview was shown: the rules then run by themselves without asking anyone. What a check
and a preview find is found on a worker, since both read the disk.
"""

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from filehouse.core import automations, locations
from filehouse.core.automations import Action, ActionKind, Condition, ConditionKind, Match, Rule
from filehouse.integration import style
from filehouse.qt_models.workers import Background
from filehouse.ui import settings

_MB = 1024 * 1024

# (kind, label, the modes with their labels)
_CONDITIONS = (
    (
        ConditionKind.NAME,
        "Name",
        (("glob", "matches"), ("contains", "contains"), ("regex", "matches the expression")),
    ),
    (ConditionKind.TYPE, "Type", (("is", "is"),)),
    (ConditionKind.SIZE, "Size", (("larger", "is larger than"), ("smaller", "is smaller than"))),
    (ConditionKind.AGE, "Modified", (("older", "more than"), ("newer", "less than"))),
)
_ACTIONS = (
    (ActionKind.RENAME, "Rename to"),
    (ActionKind.MOVE, "Move to"),
    (ActionKind.COPY, "Copy to"),
    (ActionKind.TRASH, "Move to the trash"),
)
_HINTS = {
    ConditionKind.NAME: "*.pdf",
    ConditionKind.TYPE: ".pdf, image/*",
    ActionKind.RENAME: "{date} {name}.{ext}",
    ActionKind.MOVE: "~/Documents",
    ActionKind.COPY: "~/Backup",
}


def new_rule():
    """What Add gives: a rule to be filled in, which cannot be saved as it is."""
    return Rule(
        "New rule",
        (Condition(ConditionKind.NAME, "glob", ""),),
        (Action(ActionKind.MOVE, ""),),
    )


def short_path(location):
    """A folder as the user would type it: "~/Documents" for one in the home folder."""
    path = locations.display_path(location)
    home = locations.display_path(locations.home())
    if path == home:
        return "~"
    if path.startswith(home + "/"):
        return "~" + path[len(home) :]
    return path


def describe(step):
    """One step of a preview: "Move to ~/Documents/Invoices (new folder)"."""
    if step.kind is ActionKind.RENAME:
        return f"Rename to “{step.target}”"
    if step.kind is ActionKind.TRASH:
        return "Move to the trash"
    verb = "Move" if step.kind is ActionKind.MOVE else "Copy"
    new = " (new folder)" if step.new_folder else ""
    return f"{verb} to {short_path(step.folder)}{new}"


class Preview(QTreeWidget):
    """What a plan would do, a row for each file it acts on, and the files that wait for
    the next run because they are still being written."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderLabels(["File", "What happens"])
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setSelectionMode(QTreeWidget.SelectionMode.NoSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.header().setStretchLastSection(True)
        self.setColumnWidth(0, style.px(220))

    def show_plan(self, plan):
        self.clear()
        for planned in plan.files:
            what = " → ".join(describe(step) for step in planned.steps)
            item = QTreeWidgetItem(self, [locations.basename(planned.source), what])
            item.setToolTip(1, what)
        for source in plan.still_changing:
            item = QTreeWidgetItem(
                self, [locations.basename(source), "Still changing: left for the next run"]
            )
            item.setDisabled(True)


def summary(plan):
    """The line under a preview."""
    count = len(plan.files)
    if not count:
        text = "The rules do nothing to the files here now."
    elif count == 1:
        text = "The rules act on 1 file here now."
    else:
        text = f"The rules act on {count} files here now."
    waiting = len(plan.still_changing)
    if waiting:
        text += f" {waiting} still changing, left for the next run."
    return text


def _choice():
    """A drop-down as wide as its longest entry: one squeezed by the field beside it
    showed "mo" for "more than", and its list was cut down to the same width."""
    combo = QComboBox()
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
    combo.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return combo


class _ConditionRow(QWidget):
    def __init__(self, condition, changed, removed):
        super().__init__()
        self.kind = _choice()
        for kind, label, _modes in _CONDITIONS:
            self.kind.addItem(label, kind)
        self.mode = _choice()
        self.text = QLineEdit()
        self.number = QDoubleSpinBox()
        self.number.setRange(0, 1_000_000)
        self.number.setDecimals(1)
        remove = QPushButton(style.icon("list-remove-symbolic", "list-remove"), "")
        remove.setToolTip("Remove this condition")
        remove.clicked.connect(lambda: removed(self))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.kind)
        layout.addWidget(self.mode)
        layout.addWidget(self.text, 1)
        layout.addWidget(self.number, 1)
        layout.addWidget(remove)

        self._set(condition)
        self.kind.currentIndexChanged.connect(self._on_kind)
        for signal in (
            self.kind.currentIndexChanged,
            self.mode.currentIndexChanged,
            self.text.textChanged,
            self.number.valueChanged,
        ):
            signal.connect(changed)

    def _set(self, condition):
        self.kind.setCurrentIndex(self.kind.findData(condition.kind))
        self._fill_modes(condition.kind)
        self.mode.setCurrentIndex(max(0, self.mode.findData(condition.mode)))
        if condition.kind in (ConditionKind.NAME, ConditionKind.TYPE):
            self.text.setText(str(condition.value))
        else:
            try:
                value = float(condition.value)
            except (TypeError, ValueError):
                value = 0.0
            self.number.setValue(value / _MB if condition.kind is ConditionKind.SIZE else value)
        self._show_fields(condition.kind)

    def _fill_modes(self, kind):
        self.mode.blockSignals(True)
        self.mode.clear()
        modes = next(modes for each, _label, modes in _CONDITIONS if each is kind)
        for mode, label in modes:
            self.mode.addItem(label, mode)
        self.mode.blockSignals(False)

    def _on_kind(self):
        kind = self.kind.currentData()
        self._fill_modes(kind)
        self._show_fields(kind)

    def _show_fields(self, kind):
        typed = kind in (ConditionKind.NAME, ConditionKind.TYPE)
        self.text.setVisible(typed)
        self.number.setVisible(not typed)
        self.text.setPlaceholderText(_HINTS.get(kind, ""))
        self.number.setSuffix(" MB" if kind is ConditionKind.SIZE else " days ago")

    def condition(self):
        kind = self.kind.currentData()
        if kind in (ConditionKind.NAME, ConditionKind.TYPE):
            value = self.text.text()
        elif kind is ConditionKind.SIZE:
            value = round(self.number.value() * _MB)
        else:
            value = self.number.value()
        return Condition(kind, self.mode.currentData(), value)


class _ActionRow(QWidget):
    def __init__(self, action, changed, removed):
        super().__init__()
        self.kind = _choice()
        for kind, label in _ACTIONS:
            self.kind.addItem(label, kind)
        self.value = QLineEdit(action.value)
        self.choose = QPushButton(style.icon("folder-open-symbolic", "folder-open"), "")
        self.choose.setToolTip("Choose a folder")
        self.choose.clicked.connect(self._choose_folder)
        remove = QPushButton(style.icon("list-remove-symbolic", "list-remove"), "")
        remove.setToolTip("Remove this action")
        remove.clicked.connect(lambda: removed(self))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.kind)
        layout.addWidget(self.value, 1)
        layout.addWidget(self.choose)
        layout.addWidget(remove)

        self.kind.setCurrentIndex(self.kind.findData(action.kind))
        self._show_fields()
        self.kind.currentIndexChanged.connect(self._show_fields)
        self.kind.currentIndexChanged.connect(changed)
        self.value.textChanged.connect(changed)

    def _show_fields(self):
        kind = self.kind.currentData()
        self.value.setVisible(kind.needs_value)
        self.value.setPlaceholderText(_HINTS.get(kind, ""))
        self.choose.setVisible(kind in (ActionKind.MOVE, ActionKind.COPY))

    def _choose_folder(self):
        # Not the desktop's dialog: with this app as the portal's, it would be this one
        start = automations.destination_folder(self.value.text()) or locations.home()
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Choose a folder",
            locations.local_path(start) or "",
            QFileDialog.Option.ShowDirsOnly | QFileDialog.Option.DontUseNativeDialog,
        )
        if chosen:
            self.value.setText(short_path(locations.from_path(chosen)))

    def action(self):
        kind = self.kind.currentData()
        return Action(kind, self.value.text().strip() if kind.needs_value else "")


class FolderRulesDialog(QDialog):
    """The rules of `folder`, to change, save and run. Saved rules are kept in the
    settings; "Save and run now" also hands what the preview shows to `jobs`.
    `stay_running` says whether the process goes on without a window, for the note on
    when rules run."""

    def __init__(self, parent, folder, jobs, stay_running=True):
        super().__init__(parent)
        self._folder = folder
        self._jobs = jobs
        self._uri = locations.uri(folder)
        self._rules = settings.folder_rules(self._uri)
        self._background = Background(self, in_turn=False)
        self._latest = [0]
        self._plan = None
        self._problems = {}  # row -> problems
        self._loading = False
        self.busy = False
        self.setWindowTitle(f"Rules for “{locations.display_name(folder)}”")
        self.setMinimumSize(style.px(760), style.px(560))

        # The list of rules
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._on_row)
        self.list.itemChanged.connect(self._on_item_changed)
        add = QPushButton(style.icon("list-add-symbolic", "list-add"), "Add")
        add.clicked.connect(self._add_rule)
        self.remove = QPushButton(style.icon("list-remove-symbolic", "list-remove"), "Remove")
        self.remove.clicked.connect(self._remove_rule)
        self.up = QPushButton(style.icon("go-up-symbolic", "go-up"), "")
        self.up.setToolTip("Move up: rules go from the first")
        self.up.clicked.connect(lambda: self._move_rule(-1))
        self.down = QPushButton(style.icon("go-down-symbolic", "go-down"), "")
        self.down.setToolTip("Move down")
        self.down.clicked.connect(lambda: self._move_rule(1))
        list_buttons = QHBoxLayout()
        for button in (add, self.remove, self.up, self.down):
            list_buttons.addWidget(button)
        list_buttons.addStretch(1)
        rules_side = QWidget()
        rules_layout = QVBoxLayout(rules_side)
        rules_layout.setContentsMargins(0, 0, 0, 0)
        rules_layout.addWidget(self.list, 1)
        rules_layout.addLayout(list_buttons)

        # The editor of the rule chosen in the list
        self.name = QLineEdit()
        self.match = QComboBox()
        self.match.addItem("all of these", Match.ALL)
        self.match.addItem("any of these", Match.ANY)
        self._conditions = QVBoxLayout()
        self._conditions.setContentsMargins(0, 0, 0, 0)
        add_condition = QPushButton(style.icon("list-add-symbolic", "list-add"), "Add condition")
        add_condition.clicked.connect(
            lambda: self._add_condition(Condition(ConditionKind.NAME, "glob", ""))
        )
        self._actions = QVBoxLayout()
        self._actions.setContentsMargins(0, 0, 0, 0)
        add_action = QPushButton(style.icon("list-add-symbolic", "list-add"), "Add action")
        add_action.clicked.connect(lambda: self._add_action(Action(ActionKind.MOVE, "")))
        self.stop = QCheckBox("Stop processing more rules")
        self.problems = QLabel()
        self.problems.setWordWrap(True)
        self.problems.setStyleSheet(f"color: {style.color('danger').name()};")

        self.editor = QWidget()
        form = QFormLayout(self.editor)
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow("Name", self.name)
        form.addRow("When a file matches", self.match)
        form.addRow(self._conditions)
        form.addRow("", add_condition)
        form.addRow(QLabel("Do this, in order:"))
        form.addRow(self._actions)
        form.addRow("", add_action)
        form.addRow(self.stop)
        form.addRow(self.problems)
        self.name.textChanged.connect(self._edited)
        self.match.currentIndexChanged.connect(self._edited)
        self.stop.toggled.connect(self._edited)

        top = QSplitter()
        top.addWidget(rules_side)
        top.addWidget(self.editor)
        top.setStretchFactor(1, 1)

        self.preview = Preview()
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.note = QLabel(self._note(stay_running))
        self.note.setWordWrap(True)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.save = self.buttons.addButton("Save", QDialogButtonBox.ButtonRole.AcceptRole)
        self.save_and_run = self.buttons.addButton(
            "Save and run now", QDialogButtonBox.ButtonRole.ActionRole
        )
        self.save.clicked.connect(self.accept)
        self.save_and_run.clicked.connect(self.run_now)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(top, 3)
        layout.addWidget(QLabel("What the rules would do now:"))
        layout.addWidget(self.preview, 2)
        layout.addWidget(self.status)
        layout.addWidget(self.note)
        layout.addWidget(self.buttons)

        for rule in self._rules:
            self._append_item(rule)
        if self._rules:
            self.list.setCurrentRow(0)
        self._show_rule(self.list.currentRow())
        self._update()

    @staticmethod
    def _note(stay_running):
        text = "Rules run when Filehouse starts, once a day while it runs, and when you run them."
        if not stay_running:
            text += (
                " Filehouse is set to end when its last window closes, so rules only run "
                "while a window is open."
            )
        return text

    @property
    def rules(self):
        return list(self._rules)

    # The list

    def _append_item(self, rule):
        item = QListWidgetItem(rule.name)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if rule.enabled else Qt.CheckState.Unchecked)
        item.setToolTip("Untick to keep the rule without running it")
        self.list.addItem(item)

    def _add_rule(self):
        self._rules.append(new_rule())
        self._append_item(self._rules[-1])
        self.list.setCurrentRow(len(self._rules) - 1)
        self.name.selectAll()
        self.name.setFocus()
        self._update()

    def _remove_rule(self):
        row = self.list.currentRow()
        if row < 0:
            return
        del self._rules[row]
        self.list.takeItem(row)
        self._show_rule(self.list.currentRow())
        self._update()

    def _move_rule(self, by):
        row = self.list.currentRow()
        other = row + by
        if row < 0 or not 0 <= other < len(self._rules):
            return
        self._rules[row], self._rules[other] = self._rules[other], self._rules[row]
        self._loading = True
        item = self.list.takeItem(row)
        self.list.insertItem(other, item)
        self._loading = False
        self.list.setCurrentRow(other)
        self._update()

    def _on_row(self, row):
        self._show_rule(row)

    def _on_item_changed(self, item):
        if self._loading:
            return
        row = self.list.row(item)
        if 0 <= row < len(self._rules):
            enabled = item.checkState() == Qt.CheckState.Checked
            if enabled != self._rules[row].enabled:
                self._rules[row] = replace(self._rules[row], enabled=enabled)
                self._update()

    # The editor

    def _show_rule(self, row):
        has = 0 <= row < len(self._rules)
        self.editor.setEnabled(has)
        self.remove.setEnabled(has)
        self.up.setEnabled(has and row > 0)
        self.down.setEnabled(has and row < len(self._rules) - 1)
        self._loading = True
        _clear(self._conditions)
        _clear(self._actions)
        rule = self._rules[row] if has else Rule("", (), ())
        self.name.setText(rule.name)
        self.match.setCurrentIndex(self.match.findData(rule.match))
        self.stop.setChecked(rule.stop)
        for condition in rule.conditions:
            self._add_condition(condition)
        for action in rule.actions:
            self._add_action(action)
        self._loading = False
        self._show_problems()

    def _add_condition(self, condition):
        self._conditions.addWidget(_ConditionRow(condition, self._edited, self._remove_row))
        self._edited()

    def _add_action(self, action):
        self._actions.addWidget(_ActionRow(action, self._edited, self._remove_row))
        self._edited()

    def _remove_row(self, row):
        row.setParent(None)
        row.deleteLater()
        self._edited()

    def _edited(self, *_args):
        if self._loading:
            return
        row = self.list.currentRow()
        if not 0 <= row < len(self._rules):
            return
        rule = Rule(
            self.name.text(),
            tuple(widget.condition() for widget in _rows(self._conditions)),
            tuple(widget.action() for widget in _rows(self._actions)),
            match=self.match.currentData(),
            enabled=self._rules[row].enabled,
            stop=self.stop.isChecked(),
        )
        if rule == self._rules[row]:
            return
        self._rules[row] = rule
        self._loading = True
        self.list.item(row).setText(rule.name or "(no name)")
        self._loading = False
        self._update()

    # Checking and the preview, on a worker

    def _update(self):
        rules, folder = list(self._rules), self._folder
        self._latest[0] += 1
        asked, latest = self._latest[0], self._latest

        def find():
            if latest[0] != asked:
                return None
            problems = {row: automations.check_rule(rule, folder) for row, rule in enumerate(rules)}
            return problems, automations.plan_run(folder, rules)

        self.busy = True
        self._set_buttons()
        self.status.setText("Looking at the files here…")
        self._background.call(
            find, lambda found: self._found(asked, found), lambda error: self._failed(asked, error)
        )

    def _found(self, asked, found):
        if asked != self._latest[0] or found is None or self._jobs is None:
            return
        self.busy = False
        self._problems, self._plan = found
        self.preview.show_plan(self._plan)
        danger = style.color("danger")
        for row in range(self.list.count()):
            bad = bool(self._problems.get(row))
            self.list.item(row).setData(Qt.ItemDataRole.ForegroundRole, danger if bad else None)
        self._show_problems()
        broken = sum(bool(problems) for problems in self._problems.values())
        text = summary(self._plan)
        if broken:
            text = f"{_count(broken, 'rule')} cannot work yet and is left out. " + text
        self.status.setText(text)
        self._set_buttons()

    def _failed(self, asked, error):
        if asked != self._latest[0] or self._jobs is None:
            return
        self.busy = False
        self._plan = None
        self.preview.clear()
        self.status.setText(f"The files here cannot be read: {error}")
        self._set_buttons()

    def _show_problems(self):
        problems = self._problems.get(self.list.currentRow(), [])
        self.problems.setText("\n".join(problems))
        self.problems.setVisible(bool(problems))

    def _set_buttons(self):
        ready = not self.busy and self._plan is not None
        can_save = ready and not any(self._problems.values())
        self.save.setEnabled(can_save)
        self.save_and_run.setEnabled(can_save and bool(self._plan.files) if ready else False)

    # Saving

    def done(self, result):
        # The model belongs to the app; don't hold on to it past closing
        self._jobs = None
        super().done(result)

    def accept(self):
        if self._jobs is None or not self.save.isEnabled():
            return
        settings.set_folder_rules(self._uri, self._rules)
        super().accept()

    def run_now(self):
        if self._jobs is None or not self.save_and_run.isEnabled():
            return
        settings.set_folder_rules(self._uri, self._rules)
        self._jobs.run_rules(self._folder, self._plan, origin=self.parent())
        super().accept()


class RunRulesDialog(QDialog):
    """ "Run rules now": what the saved rules of `folder` would do, and Run."""

    def __init__(self, parent, folder, jobs):
        super().__init__(parent)
        self._folder = folder
        self._jobs = jobs
        self._plan = None
        self._background = Background(self, in_turn=False)
        self.setWindowTitle(f"Run the rules of “{locations.display_name(folder)}”")
        self.setMinimumSize(style.px(560), style.px(380))
        self.preview = Preview()
        self.status = QLabel("Looking at the files here…")
        self.status.setWordWrap(True)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.run = self.buttons.addButton("Run", QDialogButtonBox.ButtonRole.AcceptRole)
        self.run.setEnabled(False)
        self.run.clicked.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.preview, 1)
        layout.addWidget(self.status)
        layout.addWidget(self.buttons)

        rules = settings.folder_rules(locations.uri(folder))
        self._background.call(
            lambda: automations.plan_run(folder, rules), self._found, self._failed
        )

    def _found(self, plan):
        if self._jobs is None:
            return
        self._plan = plan
        self.preview.show_plan(plan)
        text = summary(plan)
        if plan.problems:
            text = f"{_count(len(plan.problems), 'rule')} cannot work and is left out. " + text
        self.status.setText(text)
        self.run.setEnabled(bool(plan.files))

    def _failed(self, error):
        if self._jobs is not None:
            self.status.setText(f"The files here cannot be read: {error}")

    def done(self, result):
        self._jobs = None
        super().done(result)

    def accept(self):
        if self._jobs is None or self._plan is None or not self._plan.files:
            return
        self._jobs.run_rules(self._folder, self._plan, origin=self.parent())
        super().accept()


def _rows(layout):
    return [
        layout.itemAt(index).widget()
        for index in range(layout.count())
        if layout.itemAt(index).widget() is not None
        and layout.itemAt(index).widget().parent() is not None
    ]


def _clear(layout):
    while layout.count():
        widget = layout.takeAt(0).widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()


def _count(count, noun):
    return f"1 {noun}" if count == 1 else f"{count} {noun}s"
