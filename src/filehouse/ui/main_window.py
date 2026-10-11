"""One browser window: toolbar, sidebar, file list, operations panel and status bar."""

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from filehouse.core import locations
from filehouse.core.errors import OperationError
from filehouse.core.history import History
from filehouse.core.operations import JobKind, quick
from filehouse.core.operations.conflicts import split_extension
from filehouse.integration import desktop, style
from filehouse.qt_models.directory_model import DirectoryModel, DirectorySortProxy
from filehouse.qt_models.job_model import JobModel
from filehouse.qt_models.volumes import Volumes
from filehouse.ui import clipboard, menus, settings
from filehouse.ui.dialogs.name import NameDialog
from filehouse.ui.dialogs.prompter import Prompter
from filehouse.ui.dialogs.properties import PropertiesDialog
from filehouse.ui.folder_view import FolderView
from filehouse.ui.operations_panel import OperationsPanel
from filehouse.ui.path_bar import PathBar
from filehouse.ui.sidebar import Sidebar

_MESSAGE_MS = 8000
DETAILS, ICONS = settings.DETAILS, settings.ICONS

KEEP_RUNNING, CANCEL_ALL, STAY_OPEN = range(3)


def ask_about_running_jobs(parent, count):
    """What to do with the operations still running when the last window closes."""
    operations = "1 operation is" if count == 1 else f"{count} operations are"
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle("Operations are running")
    box.setText(f"{operations} still running.")
    box.setInformativeText("They can finish in the background. The app closes when they are done.")
    keep = box.addButton("Keep running", QMessageBox.ButtonRole.AcceptRole)
    cancel = box.addButton(
        "Cancel 1 operation" if count == 1 else f"Cancel {count} operations",
        QMessageBox.ButtonRole.DestructiveRole,
    )
    stay = box.addButton("Don't close", QMessageBox.ButtonRole.RejectRole)
    box.setEscapeButton(stay)
    box.exec()
    return {keep: KEEP_RUNNING, cancel: CANCEL_ALL}.get(box.clickedButton(), STAY_OPEN)


def quit_app():
    QApplication.quit()


class MainWindow(QMainWindow):
    def __init__(self, location, select=None, message=None, jobs=None):
        """`jobs` is the process's JobModel. A window made without one gets its own."""
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowIcon(QIcon.fromTheme("system-file-manager"))
        self.resize(settings.window_size(QSize(style.px(960), style.px(620))))

        self._history = History()
        self._location = None
        self._pending_select = None
        self._error = None
        self._closing_when_idle = False

        if jobs is None:
            jobs = JobModel(self)
            self._prompter = Prompter(jobs, self)
        self._jobs = jobs
        self._jobs.idle.connect(self._on_jobs_idle)

        self._model = DirectoryModel(self)
        self._proxy = DirectorySortProxy(self)
        self._proxy.setSourceModel(self._model)
        self._proxy.set_show_hidden(settings.show_hidden())
        self._model.loaded.connect(self._on_loaded)
        self._model.load_failed.connect(self._on_load_failed)

        self._volumes = Volumes(self)
        self._volumes.removed.connect(self._on_volume_removed)
        self._volumes.failed.connect(self._show_error)
        self._sidebar = Sidebar(locations.places(), self._volumes)
        self._sidebar.place_activated.connect(self.navigate)
        self._view = FolderView(self._proxy)
        self._view.item_activated.connect(self._on_item_activated)
        self._view.context_menu_requested.connect(self._on_context_menu)
        self._view.selection_model().selectionChanged.connect(self._update_state)
        self._view.dropped.connect(self._on_dropped)
        self._panel = OperationsPanel(self._jobs)

        splitter = QSplitter()
        splitter.addWidget(self._sidebar)
        splitter.addWidget(self._view)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([style.px(200), style.px(760)])
        splitter.setHandleWidth(style.px(8))
        splitter.setContentsMargins(style.px(8), 0, style.px(8), style.px(4))
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(splitter, 1)
        layout.addWidget(self._panel)
        self.setCentralWidget(central)

        # Item counts sit on the right; the left is for temporary messages
        self._count_label = QLabel()
        self.statusBar().addPermanentWidget(self._count_label)
        self.statusBar().setSizeGripEnabled(False)

        self._path_bar = PathBar()
        self._path_bar.location_chosen.connect(self.navigate)
        self._path_bar.text_entered.connect(self._on_path_entered)
        self._create_actions()
        QApplication.clipboard().dataChanged.connect(self._update_state)

        for signal in (
            self._proxy.rowsInserted,
            self._proxy.rowsRemoved,
            # The proxy drops a row before the model has: without this, a folder whose
            # last file goes would be told it only has hidden files
            self._model.rowsRemoved,
            self._proxy.modelReset,
            self._proxy.layoutChanged,
        ):
            signal.connect(self._update_state)

        self.set_view_mode(settings.view_mode(), remember=False)
        self.navigate(location, select=select)
        if message:
            self.statusBar().showMessage(message, _MESSAGE_MS)
        self._view.setFocus()

    def _create_actions(self):
        def action(text, icon, shortcuts, slot, colour="textPrimary"):
            item = QAction(style.icon(icon + "-symbolic", icon, colour=colour), text, self)
            item.setShortcuts(shortcuts)
            item.triggered.connect(slot)
            self.addAction(item)
            return item

        # Backspace goes back, as in Dolphin
        self._back = action(
            "Back",
            "go-previous",
            [QKeySequence("Alt+Left"), QKeySequence("Backspace")],
            self.go_back,
        )
        self._forward = action("Forward", "go-next", [QKeySequence("Alt+Right")], self.go_forward)
        self._up = action("Up", "go-up", [QKeySequence("Alt+Up")], self.go_up)
        action("Edit location", "edit-entry", [QKeySequence("Ctrl+L")], self._path_bar.edit)
        action("Reload", "view-refresh", [QKeySequence("F5")], self.reload)
        action("Close", "window-close", [QKeySequence("Ctrl+W")], self.close)
        self._hidden = action(
            "Show hidden files",
            "view-hidden",
            [QKeySequence("Ctrl+H")],
            self._set_show_hidden,
        )
        self._hidden.setCheckable(True)
        self._hidden.setChecked(self._proxy.show_hidden)

        # The same keys as Dolphin
        self._icons_mode = action(
            "Icons", "view-list-icons", [QKeySequence("Ctrl+1")], lambda: self.set_view_mode(ICONS)
        )
        self._details_mode = action(
            "Details",
            "view-list-details",
            [QKeySequence("Ctrl+3")],
            lambda: self.set_view_mode(DETAILS),
        )
        modes = QActionGroup(self)
        for item in (self._icons_mode, self._details_mode):
            item.setCheckable(True)
            modes.addAction(item)
        self._details_mode.setChecked(True)

        keys = QKeySequence.StandardKey
        self._copy = action("Copy", "edit-copy", QKeySequence.keyBindings(keys.Copy), self.copy)
        # Not the platform's whole list for Cut: it includes Shift+Delete
        self._cut = action("Cut", "edit-cut", [QKeySequence("Ctrl+X")], self.cut)
        self._paste = action(
            "Paste", "edit-paste", QKeySequence.keyBindings(keys.Paste), self.paste
        )
        self._rename = action("Rename…", "edit-rename", [QKeySequence("F2")], self.rename)
        self._trash = action(
            "Move to trash",
            "user-trash",
            [QKeySequence("Delete")],
            self.trash_selected,
            colour="danger",
        )
        self._delete = action(
            "Delete permanently…",
            "edit-delete",
            [QKeySequence("Shift+Delete")],
            self.delete_selected,
            colour="danger",
        )
        self._new_folder = action(
            "New folder…", "folder-new", [QKeySequence("Ctrl+Shift+N")], self.new_folder
        )
        self._restore = action("Restore", "edit-undo", [], self.restore_selected)
        self._empty_trash = action("Empty trash…", "trash-empty", [], self.empty_trash)
        self._properties = action(
            "Properties", "document-properties", [QKeySequence("Alt+Return")], self.properties
        )

        # One row of rounded groups: where you are, what you can do, how it is shown
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        toolbar.addWidget(self._group(self._back, self._forward, self._up))
        location = self._group()
        location.layout().addWidget(self._path_bar)
        location.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(location)
        toolbar.addWidget(self._group(self._copy, self._rename, self._trash))
        toolbar.addWidget(self._group(self._details_mode, self._icons_mode))
        self.addToolBar(toolbar)

    def _group(self, *actions):
        """A rounded group for the toolbar, holding an icon button per action."""
        group = QFrame()
        group.setObjectName("group")
        layout = QHBoxLayout(group)
        layout.setContentsMargins(style.px(4), style.px(4), style.px(4), style.px(4))
        layout.setSpacing(style.px(2))
        for item in actions:
            button = QToolButton()
            button.setDefaultAction(item)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            button.setIconSize(QSize(style.px(18), style.px(18)))
            side = style.px(style.CONTROL_HEIGHT)
            button.setFixedSize(side, side)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            layout.addWidget(button)
        return group

    # What is remembered

    def set_view_mode(self, mode, remember=True):
        self._view.set_mode(mode)
        (self._icons_mode if mode == ICONS else self._details_mode).setChecked(True)
        if remember:
            settings.set_view_mode(mode)

    def _set_show_hidden(self, show):
        self._proxy.set_show_hidden(show)
        settings.set_show_hidden(show)

    # Navigation

    def navigate(self, location, select=None):
        """Go to a folder and record it in the history."""
        if locations.same(location, self._location):
            return
        self._history.visit(location)
        self._show(location, select)

    def go_back(self):
        if self._history.can_go_back:
            previous = self._location
            self._show(self._history.back(), self._name_within(previous, self._history.current))

    def go_forward(self):
        if self._history.can_go_forward:
            self._show(self._history.forward())

    def go_up(self):
        parent = locations.parent(self._location)
        if parent is not None:
            # Arrive with the folder we came from selected
            self.navigate(parent, select=locations.basename(self._location))

    def reload(self):
        self._show(self._location)

    def _show(self, location, select=None):
        self._location = location
        self._pending_select = select
        self._error = None
        self._model.set_location(location)

        self.setWindowTitle(f"{locations.display_name(location)} — Files")
        self._path_bar.set_location(locations.segments(location), locations.display_path(location))
        self._sidebar.select(location)
        self._back.setEnabled(self._history.can_go_back)
        self._forward.setEnabled(self._history.can_go_forward)
        self._up.setEnabled(locations.parent(location) is not None)
        self._update_state()

    @staticmethod
    def _name_within(location, folder):
        """The name of `location` if it sits directly in `folder`, to select it there."""
        if locations.same(locations.parent(location), folder):
            return locations.basename(location)
        return None

    def _on_volume_removed(self, root):
        """A drive went away. A window that was looking at it has nothing left to show."""
        if locations.same(self._location, root) or locations.inside(self._location, root):
            self.navigate(locations.home())
            self._show_error("The drive this window was showing is gone.")

    def _on_path_entered(self, text):
        target = locations.resolve_start(text)
        if target.message:
            self.statusBar().showMessage(target.message, _MESSAGE_MS)
            return
        if locations.same(target.location, self._location):
            if target.select:
                self._view.select_name(target.select)
        else:
            self.navigate(target.location, select=target.select)
        self._view.setFocus()

    # Model state

    def _on_loaded(self):
        self._select_pending()
        self._update_state()

    def _select_pending(self):
        name = self._pending_select
        if name and self._model.row_for_name(name) >= 0:
            self._view.select_name(name)
            self._pending_select = None

    def _on_load_failed(self, message):
        self._error = message
        self._update_state()

    def _update_state(self, *_args):
        # Something this window made (a new folder, a renamed file) is selected once it is listed
        if not self._model.loading:
            self._select_pending()
        infos = self._view.selected_infos()
        selected = len(infos)
        in_trash = self._in_trash()
        at_trash = self._at_trash()
        self._copy.setEnabled(selected > 0)
        self._properties.setEnabled(True)
        # What is in the trash can be restored or deleted, and nothing else
        self._cut.setEnabled(selected > 0 and not in_trash)
        # In the trash the Delete key deletes for good (and asks first, as always)
        for item in (self._trash, self._delete):
            item.setEnabled(selected > 0 and (at_trash or not in_trash))
        self._rename.setEnabled(selected == 1 and not in_trash)
        self._paste.setEnabled(bool(clipboard.get()[0]) and not in_trash)
        self._new_folder.setEnabled(not in_trash)
        self._restore.setEnabled(
            selected > 0 and all(info.original_path is not None for info in infos)
        )
        self._empty_trash.setEnabled(at_trash and self._model.rowCount() > 0)

        count = self._proxy.rowCount()
        if self._error:
            self._view.show_message(self._error)
        elif count or self._model.loading:
            self._view.show_items()
        elif self._model.rowCount():
            self._view.show_message("Only hidden files here. Press Ctrl+H to show them.")
        elif at_trash:
            self._view.show_message("The trash is empty")
        else:
            self._view.show_message("This folder is empty")

        if self._error:
            text = ""
        elif self._model.loading:
            text = "Loading…"
        else:
            text = "1 item" if count == 1 else f"{count} items"
            if selected == 1:
                text += f" · {infos[0].display_name} selected"
            elif selected:
                text += f" · {selected} selected"
        self._count_label.setText(text)

    # Items

    def _in_trash(self):
        return self._location is not None and locations.in_trash(self._location)

    def _at_trash(self):
        return self._location is not None and locations.is_trash(self._location)

    def _on_item_activated(self, info):
        target = locations.child(self._location, info.name)
        if info.is_dir:
            self.navigate(target)
        elif self._in_trash():
            self._show_error(f"Restore “{info.display_name}” to open it.")
        else:
            desktop.open_default(target, self._show_error)

    def _show_error(self, message):
        self.statusBar().showMessage(message, _MESSAGE_MS)

    def _on_context_menu(self, info, position):
        menu = menus.menu(self)
        if self._in_trash():
            if info is not None:
                menu.addActions([self._restore, self._delete])
                menu.addSeparator()
                menu.addAction(self._properties)
            else:
                menu.addAction(self._empty_trash)
            menu.exec(position)
            return
        folder = self._location
        if info is not None:
            target = locations.child(self._location, info.name)
            menu.addAction(
                style.icon("document-open-symbolic", "document-open"),
                "Open",
                lambda: self._on_item_activated(info),
            )
            if info.is_dir:
                folder = target
            else:
                apps = desktop.apps_for_type(info.content_type)
                if apps:
                    open_with = menus.menu(menu, "Open with")
                    open_with.setIcon(style.icon("view-grid-symbolic", "view-grid"))
                    menu.addMenu(open_with)
                    for app in apps:
                        open_with.addAction(
                            QIcon.fromTheme(app.icon) if app.icon else QIcon(),
                            app.name,
                            lambda app=app: desktop.open_with(app, target, self._show_error),
                        )
        manager = desktop.file_manager()
        if manager is not None:
            menu.addAction(
                style.icon("folder-open-symbolic", "folder-open"),
                f"Open folder in {manager.name}",
                lambda: desktop.open_with(manager, folder, self._show_error),
            )
        menu.addSeparator()
        if info is not None:
            menu.addActions([self._cut, self._copy])
        menu.addAction(self._paste)
        if info is not None:
            menu.addSeparator()
            menu.addActions([self._rename, self._trash, self._delete])
        else:
            menu.addAction(self._new_folder)
            menu.addSeparator()
            menu.addAction(self._hidden)
        menu.addSeparator()
        menu.addAction(self._properties)
        menu.exec(position)

    # File operations

    def _selected_locations(self):
        return [locations.child(self._location, info.name) for info in self._view.selected_infos()]

    def _start(self, kind, sources, destination=None):
        return self._jobs.start(kind, sources, destination, origin=self)

    def copy(self):
        self._to_clipboard(cut=False)

    def cut(self):
        self._to_clipboard(cut=True)

    def _to_clipboard(self, cut):
        selected = self._selected_locations()
        if selected:
            clipboard.put([locations.uri(location) for location in selected], cut)

    def paste(self):
        """Copy what is on the clipboard into this folder, or move it if it was cut."""
        uris, cut = clipboard.get()
        if not uris or self._in_trash():
            return None
        sources = [locations.from_uri(uri) for uri in uris]
        if cut:
            # A cut is used up by its paste
            clipboard.clear()
            if self._all_in(sources, self._location):
                return None
        return self._start(JobKind.MOVE if cut else JobKind.COPY, sources, self._location)

    def trash_selected(self):
        if self._in_trash():
            # Already there: the Delete key means delete for good, which asks first
            return self.delete_selected()
        selected = self._selected_locations()
        return self._start(JobKind.TRASH, selected) if selected else None

    def delete_selected(self):
        """Delete for good. The job asks for confirmation before it touches anything."""
        if self._in_trash() and not self._at_trash():
            # The trash only deletes whole items, not what is inside a trashed folder
            return None
        selected = self._selected_locations()
        return self._start(JobKind.DELETE, selected) if selected else None

    def restore_selected(self):
        """Put the selected trashed items back where they were."""
        infos = [info for info in self._view.selected_infos() if info.original_path is not None]
        if not infos:
            return None
        return self._jobs.start(
            JobKind.RESTORE,
            [locations.child(self._location, info.name) for info in infos],
            origin=self,
            targets=[locations.from_path(info.original_path) for info in infos],
        )

    def empty_trash(self):
        """Delete everything in the trash for good. The job asks first, with the count."""
        if not self._at_trash() or not self._model.rowCount():
            return None
        everything = [
            locations.child(self._location, self._model.info(row).name)
            for row in range(self._model.rowCount())
        ]
        return self._start(JobKind.DELETE, everything)

    def properties(self):
        """Show the properties of the selection, or of this folder when nothing is selected."""
        selected = self._selected_locations() or [self._location]
        dialog = PropertiesDialog(selected, self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.show()
        return dialog

    def new_folder(self):
        if self._in_trash():
            return None
        folder = self._location
        name = quick.suggest_folder_name(folder)

        def create(text):
            try:
                made = quick.create_folder(folder, text)
            except OperationError as error:
                return f"Could not create the folder: {error.message}."
            self._select_when_listed(folder, locations.basename(made))
            return None

        return self._ask_name("New folder", "Name of the new folder:", name, create)

    def rename(self):
        infos = self._view.selected_infos()
        if len(infos) != 1 or self._in_trash():
            return None
        info = infos[0]
        folder = self._location
        target = locations.child(folder, info.name)

        def apply(text):
            try:
                renamed = quick.rename(target, text)
            except OperationError as error:
                return f"Could not rename “{info.display_name}”: {error.message}."
            self._select_when_listed(folder, locations.basename(renamed))
            return None

        stem = info.name if info.is_dir else split_extension(info.name)[0]
        return self._ask_name(
            "Rename", f"New name for “{info.display_name}”:", info.name, apply, len(stem)
        )

    def _ask_name(self, title, label, name, apply, select_to=None):
        dialog = NameDialog(self, title, label, name, apply, select_to)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.open()
        return dialog

    def _select_when_listed(self, folder, name):
        if locations.same(folder, self._location):
            self._pending_select = name
            self._update_state()

    @staticmethod
    def _all_in(sources, folder):
        return all(locations.same(locations.parent(source), folder) for source in sources)

    def _on_dropped(self, uris, folder_info, modifiers, position):
        """Files were dropped on the list, or on a folder in it."""
        target = self._location
        if folder_info is not None:
            target = locations.child(target, folder_info.name)
        sources = [locations.from_uri(uri) for uri in uris]
        if any(locations.same(source, target) for source in sources):
            # A folder dropped on itself
            return None
        if locations.in_trash(target):
            # Dropped on the trash: that is where they go. Not into a trashed folder
            if not self._at_trash() or folder_info is not None:
                return None
            sources = [source for source in sources if not locations.in_trash(source)]
            return self._start(JobKind.TRASH, sources) if sources else None

        keys = Qt.KeyboardModifier
        if modifiers & keys.AltModifier:
            kind = self._ask_drop_action(position)
        elif modifiers & keys.ShiftModifier:
            kind = JobKind.MOVE
        elif modifiers & keys.ControlModifier:
            kind = JobKind.COPY
        elif all(locations.same_filesystem(source, target) for source in sources):
            # As in Dolphin: a drop moves within a file system and copies across
            kind = JobKind.MOVE
        else:
            kind = JobKind.COPY
        if kind is None:
            return None
        if kind is JobKind.MOVE and self._all_in(sources, target):
            # Already where they were dropped
            return None
        return self._start(kind, sources, target)

    def _ask_drop_action(self, position):
        menu = menus.menu(self)
        move = menu.addAction(style.icon("go-jump-symbolic", "transform-move"), "Move here")
        copy = menu.addAction(style.icon("edit-copy-symbolic", "edit-copy"), "Copy here")
        menu.addSeparator()
        menu.addAction("Cancel")
        return {move: JobKind.MOVE, copy: JobKind.COPY}.get(menu.exec(position))

    # Closing

    def _other_windows_open(self):
        return any(
            widget is not self and isinstance(widget, MainWindow) and widget.isVisible()
            for widget in QApplication.topLevelWidgets()
        )

    def closeEvent(self, event):
        active = self._jobs.active_count()
        if active and not self._other_windows_open():
            # The jobs belong to the process, and this is its last window
            if not self._closing_when_idle:
                choice = ask_about_running_jobs(self, active)
                if choice == STAY_OPEN:
                    event.ignore()
                    return
                if choice == CANCEL_ALL:
                    # A request: the jobs still have to stop and clean up
                    self._jobs.cancel_all()
                self._closing_when_idle = True
            event.ignore()
            self.hide()
            return
        self._model.close()
        if self.isVisible():
            settings.set_window_size(self.size())
        super().closeEvent(event)

    def come_forward(self):
        """Show this window to the user, also when it was hidden to let jobs finish."""
        self._closing_when_idle = False
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_jobs_idle(self):
        if self._closing_when_idle:
            self._closing_when_idle = False
            self.close()
            if not self._other_windows_open():
                # Closing a hidden window doesn't end the app by itself
                QTimer.singleShot(0, quit_app)
