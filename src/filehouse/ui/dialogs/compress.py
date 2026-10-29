"""Asking what a new archive is called and which kind it is."""

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from filehouse.integration import style


class CompressDialog(QDialog):
    """`formats` are the kinds on offer, the first being the one chosen: each has a
    `key`, a `label` and an `extension`. `name(format)` suggests a name for a kind, and
    `apply(name, key)` starts the work and returns None, or what is wrong with the name."""

    def __init__(self, parent, what, formats, name, apply):
        super().__init__(parent)
        style.translucent(self)
        self._formats = formats
        self._suggest = name
        self._apply = apply
        self.setWindowTitle("Compress")
        self.setMinimumWidth(style.px(400))

        self.format = QComboBox()
        for format in formats:
            self.format.addItem(f"{format.label} ({format.extension})", format.key)
        self.edit = QLineEdit(name(formats[0]))
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {style.color('danger').name()};")
        self.error.hide()
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Compress")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.format.currentIndexChanged.connect(self._on_format_changed)
        self._extension = formats[0].extension

        form = QFormLayout()
        form.addRow("Name", self.edit)
        form.addRow("Kind", self.format)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"Compress {what} into an archive here."))
        layout.addLayout(form)
        layout.addWidget(self.error)
        layout.addWidget(self.buttons)
        self._select_stem()

    def _select_stem(self):
        # Ready to be typed over, the extension staying
        self.edit.setSelection(0, max(0, len(self.edit.text()) - len(self._extension)))

    def _on_format_changed(self, index):
        """The name follows the kind, unless it was given an ending of its own."""
        format = self._formats[index]
        text = self.edit.text()
        if text.endswith(self._extension):
            self.edit.setText(text[: len(text) - len(self._extension)] + format.extension)
        self._extension = format.extension
        self._select_stem()

    def done(self, result):
        # `apply` belongs to the window that opened this; don't hold on to it past closing
        self._apply = None
        super().done(result)

    def accept(self):
        if self._apply is None:
            return
        problem = self._apply(self.edit.text(), self.format.currentData())
        if problem is None:
            super().accept()
            return
        self.error.setText(problem)
        self.error.show()
        self.edit.setFocus()
