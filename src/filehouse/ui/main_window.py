"""One browser window: toolbar, sidebar, file list, operations panel and status bar."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSplitter,
    QToolBar,
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
from filehouse.ui import clipboard
from filehouse.ui.dialogs.name import NameDialog
from filehouse.ui.dialogs.prompter import Prompter
from filehouse.ui.folder_view import FolderView
from filehouse.ui.operations_panel import OperationsPanel
from filehouse.ui.path_bar import PathBar
from filehouse.ui.sidebar import Sidebar

_MESSAGE_MS = 8000

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
        self.resize(style.px(960), style.px(620))

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
        self._model.loaded.connect(self._on_loaded)
        self._model.load_failed.connect(self._on_load_failed)

        self._places = locations.places()
        self._sidebar = Sidebar(self._places)
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
        splitter.setSizes([style.px(190), style.px(770)])
        splitter.setHandleWidth(1)
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
            self._proxy.modelReset,
            self._proxy.layoutChanged,
        ):
            signal.connect(self._update_state)

        self.navigate(location, select=select)
        if message:
            self.statusBar().showMessage(message, _MESSAGE_MS)
        self._view.setFocus()

    def _create_actions(self):
        def action(text, icon, shortcuts, slot):
            item = QAction(QIcon.fromTheme(icon), text, self)
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
            self._proxy.set_show_hidden,
        )
        self._hidden.setCheckable(True)

        keys = QKeySequence.StandardKey
        self._copy = action("Copy", "edit-copy", QKeySequence.keyBindings(keys.Copy), self.copy)
        # Not the platform's whole list for Cut: it includes Shift+Delete
        self._cut = action("Cut", "edit-cut", [QKeySequence("Ctrl+X")], self.cut)
        self._paste = action(
            "Paste", "edit-paste", QKeySequence.keyBindings(keys.Paste), self.paste
        )
        self._rename = action("Rename…", "edit-rename", [QKeySequence("F2")], self.rename)
        self._trash = action(
            "Move to trash", "user-trash", [QKeySequence("Delete")], self.trash_selected
        )
        self._delete = action(
            "Delete permanently…",
            "edit-delete",
            [QKeySequence("Shift+Delete")],
            self.delete_selected,
        )
        self._new_folder = action(
            "New folder…", "folder-new", [QKeySequence("Ctrl+Shift+N")], self.new_folder
        )

        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        toolbar.addActions([self._back, self._forward, self._up])
        toolbar.addWidget(self._path_bar)
        toolbar.addAction(self._hidden)
        self.addToolBar(toolbar)

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
        self._sidebar.select(
            next(
                (
                    index
                    for index, place in enumerate(self._places)
                    if locations.same(place.location, location)
                ),
                -1,
            )
        )
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
        selected = self._view.selected_count()
        for item in (self._copy, self._cut, self._trash, self._delete):
            item.setEnabled(selected > 0)
        self._rename.setEnabled(selected == 1)
        self._paste.setEnabled(bool(clipboard.get()[0]))

        count = self._proxy.rowCount()
        if self._error:
            self._view.show_message(self._error)
        elif count or self._model.loading:
            self._view.show_items()
        elif self._model.rowCount():
            self._view.show_message("Only hidden files here. Press Ctrl+H to show them.")
        else:
            self._view.show_message("This folder is empty")

        if self._error:
            text = ""
        elif self._model.loading:
            text = "Loading…"
        else:
            text = "1 item" if count == 1 else f"{count} items"
            if selected:
                text += f", {selected} selected"
        self._count_label.setText(text)

    # Items

    def _on_item_activated(self, info):
        target = locations.child(self._location, info.name)
        if info.is_dir:
            self.navigate(target)
        else:
            desktop.open_default(target, self._show_error)

    def _show_error(self, message):
        self.statusBar().showMessage(message, _MESSAGE_MS)

    def _on_context_menu(self, info, position):
        menu = QMenu(self)
        folder = self._location
        if info is not None:
            target = locations.child(self._location, info.name)
            menu.addAction("Open", lambda: self._on_item_activated(info))
            if info.is_dir:
                folder = target
            else:
                apps = desktop.apps_for_type(info.content_type)
                if apps:
                    open_with = menu.addMenu("Open with")
                    for app in apps:
                        open_with.addAction(
                            QIcon.fromTheme(app.icon) if app.icon else QIcon(),
                            app.name,
                            lambda app=app: desktop.open_with(app, target, self._show_error),
                        )
        manager = desktop.file_manager()
        if manager is not None:
            menu.addAction(
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
        if not uris:
            return None
        sources = [locations.from_uri(uri) for uri in uris]
        if cut:
            # A cut is used up by its paste
            clipboard.clear()
            if self._all_in(sources, self._location):
                return None
        return self._start(JobKind.MOVE if cut else JobKind.COPY, sources, self._location)

    def trash_selected(self):
        selected = self._selected_locations()
        return self._start(JobKind.TRASH, selected) if selected else None

    def delete_selected(self):
        """Delete for good. The job asks for confirmation before it touches anything."""
        selected = self._selected_locations()
        return self._start(JobKind.DELETE, selected) if selected else None

    def new_folder(self):
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
        if len(infos) != 1:
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
        menu = QMenu(self)
        move = menu.addAction(QIcon.fromTheme("transform-move"), "Move here")
        copy = menu.addAction(QIcon.fromTheme("edit-copy"), "Copy here")
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
        super().closeEvent(event)

    def _on_jobs_idle(self):
        if self._closing_when_idle:
            self._closing_when_idle = False
            self.close()
            if not self._other_windows_open():
                # Closing a hidden window doesn't end the app by itself
                QTimer.singleShot(0, quit_app)
