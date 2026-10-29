"""Asking for a name: rename and new folder."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QLineEdit, QVBoxLayout

from filehouse.integration import style

# What `apply` returns when the answer comes later, through `NameDialog.answer`
LATER = object()


class NameDialog(QDialog):
    """Asks for a name and gives it to `apply(name)`, which returns None or what went wrong.
    The dialog stays open, showing the reason, until a name works or it is cancelled.

    `apply` can also return LATER, when what the name is for is done on another thread:
    the dialog waits, and takes the answer from `answer(...)`."""

    def __init__(self, parent, title, label, name, apply, select_to=None):
        super().__init__(parent)
        style.translucent(self)
        self._apply = apply
        self.busy = False
        self.setWindowTitle(title)
        self.setMinimumWidth(style.px(380))

        self.edit = QLineEdit(name)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {style.color('danger').name()};")
        self.error.hide()
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(label))
        layout.addWidget(self.edit)
        layout.addWidget(self.error)
        layout.addWidget(self.buttons)

        # For a file, select the name without the extension, ready to be typed over
        if select_to:
            self.edit.setSelection(0, select_to)
        else:
            self.edit.selectAll()

    def done(self, result):
        # `apply` belongs to the window that opened this; don't hold on to it past closing
        self._apply = None
        super().done(result)

    def accept(self):
        if self._apply is None or self.busy:
            return
        problem = self._apply(self.edit.text())
        if problem is LATER:
            self._set_busy(True)
            return
        self._show(problem)

    def answer(self, problem):
        """The answer `apply` said would come later: None, or what went wrong. Nothing
        once the dialog is closed: what was done stays done."""
        if self._apply is None:
            return
        self._set_busy(False)
        self._show(problem)

    def suggest(self, name):
        """A better name than the one the dialog opened with, for when it is worked out
        on another thread. Not once the user has typed, or asked for one already."""
        if self._apply is None or self.busy or self.edit.isModified():
            return
        self.edit.setText(name)
        self.edit.selectAll()

    def _show(self, problem):
        if problem is None:
            super().accept()
            return
        self.error.setText(problem)
        self.error.show()
        self.edit.setFocus()

    def _set_busy(self, busy):
        self.busy = busy
        self.edit.setEnabled(not busy)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not busy)


def ask_name(parent, title, label, name, apply, select_to=None):
    """Open a NameDialog that goes away once it is closed, and return it."""
    dialog = NameDialog(parent, title, label, name, apply, select_to)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dialog.open()
    return dialog
