"""The dialog other apps open and save with: the list and the sidebar of the app, without
what it does to files.

A `Chooser` is made for one request of the file chooser portal (`integration/portal.py`)
and answers it: with what was chosen, or as cancelled when it is closed. It is the parts
the browser window is made of (a `Pane`, the sidebar, the path bar, the search field and
the preview), with a name to type for saving, the file types the app asked for and its
own questions under the list.
"""

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QAction, QActionGroup, QKeyEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
)

from filehouse.core import locations
from filehouse.core.errors import OperationError
from filehouse.core.operations import quick
from filehouse.core.operations.conflicts import free_name, split_extension
from filehouse.integration import parent_window, portal, style
from filehouse.qt_models.volumes import Volumes
from filehouse.ui import groups, menus, settings
from filehouse.ui.dialogs.name import NameDialog
from filehouse.ui.pane import Pane
from filehouse.ui.path_bar import PathBar
from filehouse.ui.preview import PreviewWindow
from filehouse.ui.search_field import SearchField
from filehouse.ui.sidebar import Sidebar

_SEARCH_WIDTH = 200
_MINIMUM_SIZE = (560, 380)
_SIZE = (900, 580)
REPLACE, KEEP_BOTH = "replace", "keep both"


def ask_to_replace(parent, names, folder, keep_both=False):
    """Whether to save over what is there: REPLACE, KEEP_BOTH (offered for several files,
    which then get names that are free) or None for neither."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    if len(names) == 1:
        box.setWindowTitle("File exists")
        box.setText(f"“{names[0]}” already exists in {folder}.")
        box.setInformativeText("Replacing it overwrites what is in it now.")
    else:
        box.setWindowTitle("Files exist")
        box.setText(f"{len(names)} of these files already exist in {folder}.")
        box.setInformativeText("Replacing them overwrites what is in them now.")
    replace = box.addButton("Replace", QMessageBox.ButtonRole.DestructiveRole)
    both = box.addButton("Keep both", QMessageBox.ButtonRole.ActionRole) if keep_both else None
    cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    box.exec()
    return {replace: REPLACE, both: KEEP_BOTH}.get(box.clickedButton())


class Chooser(QDialog):
    def __init__(self, request, favourites=None, parent=None):
        """`request` is the portal's `Request`; `favourites` the process's list of
        favourite folders, shown in the sidebar when there is one."""
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowIcon(style.app_icon())
        self.setMinimumSize(style.px(_MINIMUM_SIZE[0]), style.px(_MINIMUM_SIZE[1]))
        self.resize(settings.chooser_size(QSize(style.px(_SIZE[0]), style.px(_SIZE[1]))))

        self._request = request
        request.on_closed = self.close
        self._saving = request.kind == portal.SAVE
        # A folder is what is chosen: to open, or to save several files in
        self._folders = request.kind == portal.SAVE_FILES or (
            request.kind == portal.OPEN and request.directory
        )
        self._said = ""
        self.setWindowTitle(request.title or self._default_title())

        self._pane = Pane(settings.show_hidden(), self)
        view = self._view = self._pane.view
        view.set_for_choosing(request.kind == portal.OPEN and request.multiple)
        self._pane.changed.connect(self._update)
        self._pane.location_changed.connect(self._on_location_changed)
        view.selection_model().selectionChanged.connect(self._on_selection_changed)
        view.item_activated.connect(self._on_item_activated)
        view.context_menu_requested.connect(self._on_context_menu)
        view.history_requested.connect(self._go_in_history)

        self._volumes = Volumes(self)
        self._volumes.removed.connect(self._on_volume_removed)
        self._volumes.failed.connect(self._say)
        self._sidebar = Sidebar(self._places(), self._volumes, favourites)
        self._sidebar.place_activated.connect(self.navigate)
        # The favourites are shown, and changed in the browser
        self._sidebar.setAcceptDrops(False)
        self._sidebar.setDragEnabled(False)

        self._path_bar = PathBar()
        self._path_bar.location_chosen.connect(self.navigate)
        self._path_bar.text_entered.connect(self._on_path_entered)
        self._search_field = SearchField()
        self._search_field.textChanged.connect(self._pane.set_search)
        self._search_field.cancelled.connect(self._end_search)
        self._search_field.left.connect(view.setFocus)
        self._preview = PreviewWindow(self)
        self._preview.navigate.connect(self._on_preview_key)
        self._create_actions()
        style.themed.changed.connect(self._retheme)

        top = QHBoxLayout()
        top.setSpacing(style.px(8))
        top.addWidget(groups.group(self._back, self._forward, self._up))
        location = groups.group()
        location.layout().addWidget(self._path_bar)
        location.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        top.addWidget(location, 1)
        top.addWidget(
            groups.group(self._details_mode, self._icons_mode, self._hidden, self._new_folder)
        )
        search = groups.group()
        search.layout().addWidget(self._search_field)
        search.setFixedWidth(style.px(_SEARCH_WIDTH))
        top.addWidget(search)

        splitter = QSplitter()
        splitter.addWidget(self._sidebar)
        splitter.addWidget(view)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([style.px(190), style.px(680)])
        splitter.setHandleWidth(style.px(8))

        layout = QVBoxLayout(self)
        margin = style.px(8)
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.setSpacing(style.px(8))
        layout.addLayout(top)
        layout.addWidget(splitter, 1)

        # What is saved is named here; a file clicked in the list gives its name
        self.name = None
        if self._saving:
            self.name = QLineEdit()
            self.name.textChanged.connect(self._on_name_changed)
            self.name.returnPressed.connect(self.choose)
            row = QHBoxLayout()
            row.addWidget(QLabel("Name:"))
            row.addWidget(self.name, 1)
            layout.addLayout(row)

        # The file types the app offers, and what else it asks
        self.filters = None
        self.choices = {}  # id of a Choice -> its QComboBox or QCheckBox
        options = QHBoxLayout()
        options.setSpacing(style.px(8))
        for choice in request.choices:
            self._add_choice(options, choice)
        options.addStretch(1)
        if request.filters and not self._folders:
            self.filters = QComboBox()
            for item in request.filters:
                self.filters.addItem(item.name, item)
            if request.current_filter is not None:
                self.filters.setCurrentIndex(request.filters.index(request.current_filter))
            self.filters.currentIndexChanged.connect(self._use_filter)
            options.addWidget(self.filters)
        if options.count() > 1:
            layout.addLayout(options)

        self._message = QLabel()
        self._message.setObjectName("secondary")
        self._message.setTextFormat(Qt.TextFormat.PlainText)
        self._message.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        self.accept_button = QPushButton(
            portal.accept_text(request.accept_label) or self._default_accept()
        )
        self.accept_button.clicked.connect(self.choose)
        for button in (self.cancel_button, self.accept_button):
            # Return is the list's and the name's: it goes into a folder, not out of
            # the dialog with whatever is selected
            button.setAutoDefault(False)
        buttons = QHBoxLayout()
        buttons.setSpacing(style.px(8))
        buttons.addWidget(self._message, 1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.accept_button)
        layout.addLayout(buttons)

        self._use_filter()
        self._start()

    # What is asked

    def _default_title(self):
        if self._saving:
            return "Save file"
        if self._request.kind == portal.SAVE_FILES:
            return "Save files"
        if self._folders:
            return "Choose folders" if self._request.multiple else "Choose a folder"
        return "Open files" if self._request.multiple else "Open file"

    def _default_accept(self):
        if self._saving or self._request.kind == portal.SAVE_FILES:
            return "Save"
        return "Select" if self._folders else "Open"

    def _places(self):
        """The places of the sidebar. Nothing is chosen from the trash, and nothing is
        saved in the list of recent files."""
        choosing_files = self._request.kind == portal.OPEN and not self._folders
        return [
            place
            for place in locations.places()
            if not locations.in_trash(place.location)
            and (choosing_files or not locations.in_recent(place.location))
        ]

    def _start(self):
        """Where the app says, or where a file was chosen last, or home."""
        request = self._request
        select = None
        folder = request.folder
        name = request.name
        if self._saving and request.file is not None:
            folder = locations.parent(request.file)
            select = locations.basename(request.file)
            name = name or select
        if folder is None and settings.chooser_folder():
            folder = locations.from_uri(settings.chooser_folder())
        folder = locations.nearest_folder(folder) if folder is not None else None
        self._pane.navigate(folder or locations.home(), select)
        if self.name is not None:
            self.name.setText(name)
            # Ready to be typed over, up to the extension
            self.name.setSelection(0, len(split_extension(name)[0]))
            self.name.setFocus()
        else:
            self._view.setFocus()

    def show_for_app(self):
        """Show the dialog over the window that asked for it, when that can be done."""
        # The window has to be there for the desktop before it can be made a child
        self.winId()
        attached = parent_window.attach(self.windowHandle(), self._request.parent_window)
        if attached and self._request.modal:
            self.setWindowModality(Qt.WindowModality.WindowModal)
        self.show()
        self.raise_()
        self.activateWindow()

    # The toolbar

    def _create_actions(self):
        self._icons = []

        def action(text, icon, shortcuts, slot):
            item = QAction(text, self)
            item.setShortcuts([QKeySequence(keys) for keys in shortcuts])
            item.triggered.connect(slot)
            self.addAction(item)
            self._icons.append((item, icon))
            return item

        self._back = action("Back", "go-previous", ["Alt+Left", "Backspace"], self.go_back)
        self._forward = action("Forward", "go-next", ["Alt+Right"], self.go_forward)
        self._up = action("Up", "go-up", ["Alt+Up"], self._pane.go_up)
        action("Edit location", "edit-entry", ["Ctrl+L"], self._path_bar.edit)
        action("Reload", "view-refresh", ["F5"], self._pane.reload)
        action("Search", "edit-find", ["Ctrl+F"], self._start_search)
        action("Preview", "view-preview", ["Space"], self.toggle_preview)
        self._icons_mode = action(
            "Icons", "view-list-icons", ["Ctrl+1"], lambda: self._pane.choose_mode(settings.ICONS)
        )
        self._details_mode = action(
            "Details",
            "view-list-details",
            ["Ctrl+3"],
            lambda: self._pane.choose_mode(settings.DETAILS),
        )
        modes = QActionGroup(self)
        for item in (self._icons_mode, self._details_mode):
            item.setCheckable(True)
            modes.addAction(item)
        # For this dialog: what the browser shows is its own choice
        self._hidden = action(
            "Show hidden files", "view-hidden", ["Ctrl+H"], self._pane.proxy.set_show_hidden
        )
        self._hidden.setCheckable(True)
        self._hidden.setChecked(self._pane.proxy.show_hidden)
        self._new_folder = action("New folder…", "folder-new", ["Ctrl+Shift+N"], self.new_folder)
        self._retheme()

    def _retheme(self):
        for item, icon in self._icons:
            item.setIcon(style.icon(icon + "-symbolic", icon))
        self._sidebar.retheme()
        self._search_field.retheme()

    def _add_choice(self, layout, choice):
        if choice.options:
            box = QComboBox()
            for identifier, label in choice.options:
                box.addItem(label, identifier)
            box.setCurrentIndex(max(0, box.findData(choice.default)))
            layout.addWidget(QLabel(choice.label))
        else:
            box = QCheckBox(choice.label)
            box.setChecked(choice.default == "true")
        layout.addWidget(box)
        self.choices[choice.id] = box

    def _chosen(self):
        """What the app's own questions were answered with: {id: the option's id}."""
        return {
            identifier: ("true" if box.isChecked() else "false")
            if isinstance(box, QCheckBox)
            else box.currentData()
            for identifier, box in self.choices.items()
        }

    def _filter(self):
        return self.filters.currentData() if self.filters is not None else None

    def _use_filter(self, _index=None):
        chosen = self._filter()
        if self._folders:
            # Only what can be chosen or gone into
            self._pane.proxy.set_wanted(lambda info: False)
        else:
            self._pane.proxy.set_wanted(chosen.accepts if chosen is not None else None)

    # Navigation

    def navigate(self, location, select=None):
        self._pane.navigate(location, select)

    def go_back(self):
        self._pane.go_back()

    def go_forward(self):
        self._pane.go_forward()

    def _go_in_history(self, step):
        if step < 0:
            self.go_back()
        else:
            self.go_forward()

    def _on_location_changed(self, location):
        self._said = ""
        self._path_bar.set_location(locations.segments(location), locations.display_path(location))
        self._sidebar.select(location)
        self._back.setEnabled(self._pane.history.can_go_back)
        self._forward.setEnabled(self._pane.history.can_go_forward)
        self._up.setEnabled(locations.parent(location) is not None)
        if self._search_field.text():
            self._search_field.blockSignals(True)
            self._search_field.clear()
            self._search_field.blockSignals(False)
        self._update()

    def _on_path_entered(self, text):
        target = locations.resolve_start(text)
        if target.message:
            self._say(target.message)
            return
        if locations.same(target.location, self._pane.location):
            if target.select:
                self._view.select_name(target.select)
        else:
            self.navigate(target.location, select=target.select)
        self._view.setFocus()

    def _on_volume_removed(self, root):
        location = self._pane.location
        if locations.same(location, root) or locations.inside(location, root):
            self.navigate(locations.home())
            self._say("The drive this was showing is gone.")

    def _start_search(self):
        self._search_field.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._search_field.selectAll()

    def _end_search(self):
        self._search_field.clear()
        self._view.setFocus()

    def new_folder(self):
        if self._pane.read_only:
            return None
        folder = self._pane.location

        def create(text):
            try:
                made = quick.create_folder(folder, text)
            except OperationError as error:
                return f"Could not create the folder: {error.message}."
            self._pane.select_when_listed(folder, locations.basename(made))
            return None

        dialog = NameDialog(
            self, "New folder", "Name of the new folder:", quick.suggest_folder_name(folder), create
        )
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.open()
        return dialog

    def _on_context_menu(self, _info, position):
        menu = menus.menu(self)
        menu.addAction(self._new_folder)
        menu.addAction(self._hidden)
        menu.exec(position)

    # The preview

    def toggle_preview(self):
        if self._preview.isVisible():
            self._preview.hide()
        elif self._view.selected_infos():
            self._show_preview()
            self._preview.open_over(self)

    def _show_preview(self):
        infos = self._view.selected_infos()
        if not infos:
            self._preview.show_message("Nothing selected")
        else:
            self._preview.show_items(
                infos,
                lambda info: locations.local_path(
                    locations.entry_location(self._pane.location, info)
                ),
            )

    def _on_preview_key(self, event):
        """An arrow key pressed in the preview moves the selection of the list."""
        target = self._view.focusProxy() or self._view
        QApplication.sendEvent(
            target, QKeyEvent(QEvent.Type.KeyPress, event.key(), event.modifiers())
        )

    # State

    def _say(self, text):
        """Something the user has to know, in place of the line that says what is chosen."""
        self._said = text
        self._update()

    def _on_selection_changed(self, *_args):
        self._said = ""
        infos = self._view.selected_infos()
        if self.name is not None and len(infos) == 1 and not infos[0].is_dir:
            # Saving over a file, or under a name like it
            self.name.setText(infos[0].display_name)
        if self._preview.isVisible():
            if infos:
                self._show_preview()
            else:
                self._preview.hide()
        self._update()

    def _on_name_changed(self, _text):
        self._said = ""
        self._update()

    def _update(self):
        self._pane.sync()
        mode = self._view.mode
        self._icons_mode.setChecked(mode == settings.ICONS)
        self._details_mode.setChecked(mode == settings.DETAILS)
        self._new_folder.setEnabled(not self._pane.read_only)
        if self._saving:
            ready = bool(self.name.text().strip())
        elif self._folders:
            ready = True
        else:
            ready = bool(self._view.selected_infos())
        self.accept_button.setEnabled(ready)
        self._message.setText(self._said or self._hint())

    def _hint(self):
        """Which folder the button would choose: the one selected, or the one open."""
        if not self._folders:
            return ""
        chosen = self._chosen_folders()
        if len(chosen) == 1:
            return f"The folder “{locations.display_name(chosen[0])}”"
        return f"{len(chosen)} folders"

    def _chosen_folders(self):
        folder = self._pane.location
        selected = [
            locations.entry_location(folder, info)
            for info in self._view.selected_infos()
            if info.is_dir
        ]
        return selected or [folder]

    # Choosing

    def _on_item_activated(self, info):
        target = locations.entry_location(self._pane.location, info)
        if info.is_dir:
            self.navigate(target)
        elif self._saving:
            self.name.setText(info.display_name)
            self.choose()
        elif not self._folders:
            self._open([target])

    def choose(self):
        """The button: answer with what is selected or named, or say why not."""
        if self._saving:
            self._save()
        elif self._request.kind == portal.SAVE_FILES:
            self._save_several()
        elif self._folders:
            self._open(self._chosen_folders())
        else:
            infos = self._view.selected_infos()
            folder = self._pane.location
            if len(infos) == 1 and infos[0].is_dir:
                # A folder is gone into, not opened
                self.navigate(locations.entry_location(folder, infos[0]))
                return
            self._open(
                [locations.entry_location(folder, info) for info in infos if not info.is_dir]
            )

    def _open(self, chosen):
        # The portal hands an app files of this computer and nothing else
        local = [location for location in chosen if locations.local_path(location) is not None]
        if not local:
            if chosen:
                self._say("Only what is on this computer can be chosen here.")
            return
        self._respond(local)

    def _save(self):
        text = self.name.text().strip()
        if not text:
            return
        folder = self._pane.location
        if text.startswith(("/", "~")):
            target = locations.from_user_input(text)
        else:
            # A name, or a path from here: "sub/name.txt"
            target = locations.below(folder, text)
        if locations.is_folder(target):
            # A folder was named: the file's name is still to come
            self.navigate(target)
            self.name.clear()
            self.name.setFocus()
            return
        into = locations.parent(target)
        if locations.local_path(target) is None or into is None:
            self._say("A file can only be saved on this computer.")
            return
        if not locations.is_folder(into):
            self._say(f"There is no folder “{locations.display_path(into)}”.")
            return
        if locations.file_type(target) is not None:
            name = locations.basename(target)
            if ask_to_replace(self, [name], locations.display_name(into)) != REPLACE:
                return
        self._respond([target])

    def _save_several(self):
        folder = self._chosen_folders()[0]
        if locations.local_path(folder) is None:
            self._say("Files can only be saved on this computer.")
            return
        names = list(self._request.names)
        taken = [
            name for name in names if locations.file_type(locations.child(folder, name)) is not None
        ]
        if taken:
            answer = ask_to_replace(self, taken, locations.display_name(folder), keep_both=True)
            if answer is None:
                return
            if answer == KEEP_BOTH:
                names = [
                    free_name(folder, name, is_dir=False) if name in taken else name
                    for name in names
                ]
        self._respond([locations.child(folder, name) for name in names], folder)

    def _respond(self, chosen, folder=None):
        folder = folder or locations.parent(chosen[0])
        if folder is not None:
            # Where the next dialog starts, for an app that does not say
            settings.set_chooser_folder(locations.uri(folder))
        self._request.respond(chosen, self._chosen(), self._filter())
        self.accept()

    def done(self, result):
        # Closed without a choice, by the user or because the app took the question back
        self._request.cancel()
        self._request.on_closed = lambda: None
        self._preview.hide()
        self._pane.close()
        settings.set_chooser_size(self.size())
        super().done(result)
