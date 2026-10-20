"""Asking for a name: rename and new folder."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QLineEdit, QVBoxLayout

from filehouse.integration import style


class NameDialog(QDialog):
    """Asks for a name and gives it to `apply(name)`, which returns None or what went wrong.
    The dialog stays open, showing the reason, until a name works or it is cancelled."""

    def __init__(self, parent, title, label, name, apply, select_to=None):
        super().__init__(parent)
        self._apply = apply
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
        if self._apply is None:
            return
        problem = self._apply(self.edit.text())
        if problem is None:
            super().accept()
            return
        self.error.setText(problem)
        self.error.show()
        self.edit.setFocus()


def ask_name(parent, title, label, name, apply, select_to=None):
    """Open a NameDialog that goes away once it is closed, and return it."""
    dialog = NameDialog(parent, title, label, name, apply, select_to)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    dialog.open()
    return dialog
