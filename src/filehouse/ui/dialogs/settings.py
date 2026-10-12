"""The settings of the app, in one dialog. A change is made as it is picked: there is no
OK to press, only Close."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from filehouse.integration import style
from filehouse.ui import settings

_DENSITIES = (("comfortable", "Comfortable"), ("compact", "Compact"))
_VIEWS = ((settings.DETAILS, "Details"), (settings.ICONS, "Icons"))
_MAX_SECONDS = 600


class SettingsDialog(QDialog):
    """`set_view_mode(mode)` and `set_show_hidden(show)` change the window that opened the
    dialog, which also remembers them for new windows; `clear_recent()` asks and empties
    the list of recent files."""

    def __init__(self, parent, set_view_mode, set_show_hidden, clear_recent):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(style.px(420))

        self.density = QComboBox()
        for key, text in _DENSITIES:
            self.density.addItem(text, key)
        self.density.setCurrentIndex(self.density.findData(settings.density()))
        self.density.currentIndexChanged.connect(
            lambda: settings.set_density(self.density.currentData())
        )

        self.view = QComboBox()
        for key, text in _VIEWS:
            self.view.addItem(text, key)
        self.view.setCurrentIndex(self.view.findData(settings.view_mode()))
        self.view.currentIndexChanged.connect(lambda: set_view_mode(self.view.currentData()))

        self.pane = QCheckBox("Show the details pane")
        self.pane.setChecked(settings.details_pane())
        self.pane.toggled.connect(settings.set_details_pane)
        settings.changes.details_pane_changed.connect(self._on_details_pane_changed)

        self.hidden = QCheckBox("Show hidden files")
        self.hidden.setChecked(settings.show_hidden())
        self.hidden.toggled.connect(set_show_hidden)

        self.private = QCheckBox("Don't remember opened files")
        self.private.setChecked(not settings.remember_opened())
        self.private.toggled.connect(lambda on: settings.set_remember_opened(not on))
        self.clear = QPushButton("Clear recent files…")
        self.clear.clicked.connect(clear_recent)
        # Changed from the folder menu or the status bar while this is open
        settings.changes.remember_opened_changed.connect(self._on_remember_opened_changed)

        self.notify = QCheckBox("Notify when a job ends while no window is showing")
        self.notify.setChecked(settings.notify())
        self.seconds = QSpinBox()
        self.seconds.setRange(1, _MAX_SECONDS)
        self.seconds.setSuffix(" seconds")
        self.seconds.setValue(settings.long_job_seconds())
        self.seconds.setEnabled(settings.notify())
        self.notify.toggled.connect(self._on_notify_toggled)
        self.seconds.valueChanged.connect(settings.set_long_job_seconds)

        layout = QVBoxLayout(self)
        layout.setSpacing(style.px(style.dense("card_spacing")))
        layout.addWidget(self._heading("Appearance"))
        appearance = QFormLayout()
        appearance.addRow("Density", self.density)
        layout.addLayout(appearance)
        layout.addWidget(self._note("Density applies the next time the app starts."))

        layout.addWidget(self._heading("New windows"))
        defaults = QFormLayout()
        defaults.addRow("View", self.view)
        layout.addLayout(defaults)
        layout.addWidget(self.hidden)
        layout.addWidget(self.pane)
        layout.addWidget(self._note("Changes the window you opened this from as well."))

        layout.addWidget(self._heading("Privacy"))
        layout.addWidget(self.private)
        layout.addWidget(self.clear, 0, Qt.AlignmentFlag.AlignLeft)

        layout.addWidget(self._heading("Notifications"))
        layout.addWidget(self.notify)
        threshold = QFormLayout()
        threshold.addRow("With the island showing the job, only after", self.seconds)
        layout.addLayout(threshold)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _heading(self, text):
        label = QLabel(text)
        font = label.font()
        font.setBold(True)
        label.setFont(font)
        return label

    def _note(self, text):
        label = QLabel(text)
        label.setEnabled(False)
        label.setWordWrap(True)
        return label

    def _on_notify_toggled(self, on):
        settings.set_notify(on)
        self.seconds.setEnabled(on)

    def _on_details_pane_changed(self, show):
        self.pane.blockSignals(True)
        self.pane.setChecked(show)
        self.pane.blockSignals(False)

    def _on_remember_opened_changed(self, remember):
        self.private.blockSignals(True)
        self.private.setChecked(not remember)
        self.private.blockSignals(False)

    def done(self, result):
        settings.changes.remember_opened_changed.disconnect(self._on_remember_opened_changed)
        settings.changes.details_pane_changed.disconnect(self._on_details_pane_changed)
        super().done(result)
