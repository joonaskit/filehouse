"""Every error of a failed job, in one list."""

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPlainTextEdit, QVBoxLayout

from filehouse.integration import style


class ErrorDetails(QDialog):
    def __init__(self, job, parent=None):
        super().__init__(parent)
        self.setWindowTitle("What went wrong")
        self.resize(style.px(560), style.px(320))

        self.list = QPlainTextEdit()
        self.list.setReadOnly(True)
        self.list.setPlainText(
            "\n".join(f"{error.path or error.step}: {error.message}" for error in job.errors)
        )
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(job.summary))
        layout.addWidget(self.list)
        layout.addWidget(buttons)
