"""The settings of the app, in one dialog of a few tabs. A change is made as it is
picked: there is no OK to press, only Close."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from filehouse.integration import appearance, autostart, style, theme
from filehouse.ui import settings
from filehouse.ui.choices import AccentDots, Segments

_DENSITIES = (("comfortable", "Comfortable"), ("compact", "Compact"))
_VIEWS = ((settings.DETAILS, "Details"), (settings.ICONS, "Icons"))
# A sort and the way round it goes, as one choice
_SORTS = (
    (("name", False), "Name"),
    (("name", True), "Name, Z first"),
    (("modified", True), "Modified, newest first"),
    (("modified", False), "Modified, oldest first"),
    (("size", True), "Size, largest first"),
    (("size", False), "Size, smallest first"),
)
_MAX_SECONDS = 600


class SettingsDialog(QDialog):
    """`set_show_hidden(show)` changes the window that opened the dialog, which also
    remembers it for new windows; `clear_recent()` asks and empties the list of recent
    files. The view and the sort order are the app's own, for every folder that has not
    been given another: the open windows follow them from the settings."""

    def __init__(self, parent, set_show_hidden, clear_recent):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(style.px(480))

        outer = QVBoxLayout(self)
        outer.setSpacing(style.px(style.dense("card_spacing")))
        self.tabs = QTabWidget()
        self.tabs.setObjectName("settingsTabs")
        outer.addWidget(self.tabs)
        self._pages = []

        self._appearance_page(self._page("Appearance"))
        self._folders_page(self._page("Folders"), set_show_hidden)
        general = self._page("General")
        self._starting_page(general)
        self._privacy_page(general, clear_recent)
        for layout in self._pages:
            layout.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def _page(self, title):
        """A tab; returns the layout for its settings."""
        widget = QWidget()
        widget.setObjectName("settingsPage")
        layout = QVBoxLayout(widget)
        margin = style.px(style.dense("card_margin"))
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.setSpacing(style.px(style.dense("card_spacing")))
        self.tabs.addTab(widget, title)
        self._pages.append(layout)
        return layout

    def _appearance_page(self, layout):
        """The scheme, the accent and the corners: the shell's, followed as it changes
        them, or chosen here (#30); and the density."""
        self._heading(layout, "Theme")
        self._shell = appearance.shell_found()
        self.follow = QCheckBox("Follow the shell's look")
        self.follow.setChecked(settings.follow_shell())
        self.follow.toggled.connect(self._on_follow_toggled)
        self.follow.setVisible(self._shell)
        layout.addWidget(self.follow)
        self.follow_note = self._note(
            "Quickshell Island's colours and corners, changing as you change them there. "
            "Turn this off to choose them here."
        )
        layout.addWidget(self.follow_note)
        self.scheme = Segments(appearance.SCHEMES)
        self.accent = AccentDots(appearance.ACCENTS)
        self.corners = Segments([(key, label) for key, label, _scale in appearance.CORNERS])
        for control in (self.scheme, self.accent, self.corners):
            control.chosen.connect(self._on_look_chosen)
        look = self._form()
        look.addRow("Appearance", self.scheme)
        look.addRow("Accent", self.accent)
        look.addRow("Corners", self.corners)
        layout.addLayout(look)
        self._show_look()
        # While following the shell, what it changes shows here
        style.themed.changed.connect(self._show_look)

        self.density = QComboBox()
        for key, text in _DENSITIES:
            self.density.addItem(text, key)
        self.density.setCurrentIndex(self.density.findData(settings.density()))
        self.density.currentIndexChanged.connect(
            lambda: settings.set_density(self.density.currentData())
        )
        self._heading(layout, "Layout")
        density = self._form()
        density.addRow("Density", self.density)
        layout.addLayout(density)
        layout.addWidget(
            self._note(
                "Compact takes less room for the rows, the sidebar and the toolbar. It "
                "applies once every window has been closed."
            )
        )

    def _folders_page(self, layout, set_show_hidden):
        """How folders are shown, in every window, and the switch for their rules."""
        self.view = QComboBox()
        for key, text in _VIEWS:
            self.view.addItem(text, key)
        self.view.setCurrentIndex(self.view.findData(settings.view_mode()))
        self.view.currentIndexChanged.connect(
            lambda: settings.set_view_mode(self.view.currentData())
        )

        # By its place in the list: a combo box does not find a pair again
        self._sorts = [key for key, _text in _SORTS]
        self.sort = QComboBox()
        self.sort.addItems([text for _key, text in _SORTS])
        if settings.sort() not in self._sorts:
            # Sorted by a column the list leaves out: shown as it is
            self._sorts.append(settings.sort())
            self.sort.addItem(settings.sort()[0].capitalize())
        self.sort.setCurrentIndex(self._sorts.index(settings.sort()))
        self.sort.currentIndexChanged.connect(lambda index: settings.set_sort(*self._sorts[index]))

        self.per_folder = QCheckBox("Each folder keeps the view and the sort order chosen in it")
        self.per_folder.setChecked(settings.per_folder())
        self.per_folder.toggled.connect(settings.set_per_folder)

        self._heading(layout, "View")
        form = self._form()
        form.addRow("Show as", self.view)
        form.addRow("Sort by", self.sort)
        layout.addLayout(form)
        layout.addWidget(self.per_folder)
        layout.addWidget(
            self._note("The view and the sort order above are for the folders without their own.")
        )

        self.hidden = QCheckBox("Show hidden files")
        self.hidden.setChecked(settings.show_hidden())
        self.hidden.toggled.connect(set_show_hidden)
        self.pane = QCheckBox("Show the details pane")
        self.pane.setChecked(settings.details_pane())
        self.pane.toggled.connect(settings.set_details_pane)
        settings.changes.details_pane_changed.connect(self._on_details_pane_changed)
        self._heading(layout, "In the window")
        layout.addWidget(self.hidden)
        layout.addWidget(self.pane)
        layout.addWidget(
            self._note("These change the window you opened this from, and new windows.")
        )

        # One switch that pauses the rules of every folder (#87)
        self.rules = QCheckBox("Run the rules of folders")
        self.rules.setChecked(settings.run_rules())
        self.rules.toggled.connect(settings.set_run_rules)
        self._heading(layout, "Rules")
        layout.addWidget(self.rules)
        layout.addWidget(
            self._note(
                "Rules are set in a folder's menu. They run when the app starts and once a "
                "day while it runs."
            )
        )

    def _starting_page(self, layout):
        """What a start opens, whether the process stays without a window, and what it
        tells the user when it does. The privacy goes after it on the same tab."""
        self.restore = QCheckBox("Start with the tabs of last time")
        self.restore.setChecked(settings.restore_tabs())
        self.restore.toggled.connect(settings.set_restore_tabs)
        self._heading(layout, "Windows")
        layout.addWidget(self.restore)

        self.stay = QCheckBox("Keep running without a window, so the next one opens sooner")
        self.stay.setChecked(settings.stay_running())
        self.stay.toggled.connect(self._on_stay_toggled)
        # Only a process that stays is worth starting at a login
        self.login = QCheckBox("Start it in the background when I log in")
        self.login.setChecked(settings.stay_running() and autostart.enabled())
        self.login.setEnabled(settings.stay_running())
        self.login.toggled.connect(self._on_login_toggled)
        self._heading(layout, "In the background")
        layout.addWidget(self.stay)
        layout.addWidget(self.login)

        self.notify = QCheckBox("Notify when a job ends while no window is showing")
        self.notify.setChecked(settings.notify())
        self.seconds = QSpinBox()
        self.seconds.setRange(1, _MAX_SECONDS)
        self.seconds.setSuffix(" seconds")
        self.seconds.setValue(settings.long_job_seconds())
        self.seconds.setEnabled(settings.notify())
        self.notify.toggled.connect(self._on_notify_toggled)
        self.seconds.valueChanged.connect(settings.set_long_job_seconds)
        self._heading(layout, "Notifications")
        layout.addWidget(self.notify)
        threshold = self._form()
        threshold.addRow("With the island showing the job, only after", self.seconds)
        layout.addLayout(threshold)

    def _privacy_page(self, layout, clear_recent):
        self.private = QCheckBox("Don't remember opened files")
        self.private.setChecked(not settings.remember_opened())
        self.private.toggled.connect(lambda on: settings.set_remember_opened(not on))
        self.clear = QPushButton("Clear recent files…")
        self.clear.clicked.connect(clear_recent)
        # Changed from the folder menu or the status bar while this is open
        settings.changes.remember_opened_changed.connect(self._on_remember_opened_changed)
        self._heading(layout, "Recent files")
        layout.addWidget(self.private)
        layout.addWidget(self.clear, 0, Qt.AlignmentFlag.AlignLeft)

    def _form(self):
        form = QFormLayout()
        form.setVerticalSpacing(style.px(style.dense("card_spacing")))
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return form

    def _heading(self, layout, text):
        """A section's heading, set apart from the section above it."""
        if layout.count():
            layout.addSpacing(style.px(style.dense("card_spacing")))
        label = QLabel(text)
        label.setObjectName("heading")
        layout.addWidget(label)

    def _note(self, text):
        label = QLabel(text)
        label.setObjectName("secondary")
        label.setWordWrap(True)
        return label

    def _following(self):
        return self._shell and self.follow.isChecked()

    def _show_look(self):
        """The shell's look while it is followed, greyed; or the one chosen here."""
        following = self._following()
        shown = appearance.look_of(theme.load_theme()) if following else settings.look()
        self.scheme.set_value(shown.scheme)
        self.accent.set_value(shown.accent)
        self.corners.set_value(shown.corners)
        self.follow_note.setVisible(following)
        for control in (self.scheme, self.accent, self.corners):
            control.setEnabled(not following)

    def _on_follow_toggled(self, on):
        if not on:
            # Choosing by hand starts from the shell's look as it is: nothing changes yet
            settings.set_look(appearance.look_of(theme.load_theme()))
        settings.set_follow_shell(on)
        self._apply_look()
        self._show_look()

    def _on_look_chosen(self, _key):
        settings.set_look(
            appearance.Look(self.scheme.value(), self.accent.value(), self.corners.value())
        )
        self._apply_look()

    def _apply_look(self):
        appearance.apply(QApplication.instance(), settings.chosen_look(self._shell))

    def _on_stay_toggled(self, on):
        settings.set_stay_running(on)
        if not on:
            self.login.setChecked(False)
        self.login.setEnabled(on)

    def _on_login_toggled(self, on):
        if not autostart.set_enabled(on):
            # The folder could not be written: shown as it is
            self.login.blockSignals(True)
            self.login.setChecked(autostart.enabled())
            self.login.blockSignals(False)

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
        style.themed.changed.disconnect(self._show_look)
        settings.changes.remember_opened_changed.disconnect(self._on_remember_opened_changed)
        settings.changes.details_pane_changed.disconnect(self._on_details_pane_changed)
        super().done(result)
