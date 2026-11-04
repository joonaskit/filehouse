"""Asking what to select: a pattern or a few words of the names."""

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from filehouse.integration import style


class SelectDialog(QDialog):
    """Asks for a text and gives it to `apply(text, add)`. `count(text)` says how many
    entries it would pick, which is shown as it is typed."""

    def __init__(self, parent, count, apply):
        super().__init__(parent)
        style.translucent(self)
        self._count = count
        self._apply = apply
        self.setWindowTitle("Select matching")
        self.setMinimumWidth(style.px(380))

        self.edit = QLineEdit()
        self.edit.setPlaceholderText("*.jpg, or a few words of the name")
        self.found = QLabel()
        self.found.setEnabled(False)
        self.add = QCheckBox("Add to what is selected")
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Select")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Select the items whose name matches:"))
        layout.addWidget(self.edit)
        layout.addWidget(self.found)
        layout.addWidget(self.add)
        layout.addWidget(self.buttons)

        self.edit.textChanged.connect(self._on_text)
        self._on_text("")

    def _on_text(self, text):
        found = self._count(text) if text.strip() else 0
        if not text.strip():
            self.found.setText("“*” stands for anything, “?” for one character.")
        else:
            self.found.setText("1 item matches" if found == 1 else f"{found} items match")
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(found > 0)

    def done(self, result):
        # Both belong to the window that opened this; don't hold on to them past closing
        self._count = self._apply = None
        super().done(result)

    def accept(self):
        if self._apply is None:
            return
        self._apply(self.edit.text(), self.add.isChecked())
        super().accept()
