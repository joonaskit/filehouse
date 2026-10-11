"""One browser window: toolbar, sidebar, file list and status bar."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import QLabel, QMainWindow, QMenu, QSplitter, QToolBar

from filehouse.core import locations
from filehouse.core.history import History
from filehouse.integration import desktop, style
from filehouse.qt_models.directory_model import DirectoryModel, DirectorySortProxy
from filehouse.ui.folder_view import FolderView
from filehouse.ui.path_bar import PathBar
from filehouse.ui.sidebar import Sidebar

_MESSAGE_MS = 8000


class MainWindow(QMainWindow):
    def __init__(self, location, select=None, message=None):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowIcon(QIcon.fromTheme("system-file-manager"))
        self.resize(style.px(960), style.px(620))

        self._history = History()
        self._location = None
        self._pending_select = None
        self._error = None

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

        splitter = QSplitter()
        splitter.addWidget(self._sidebar)
        splitter.addWidget(self._view)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([style.px(190), style.px(770)])
        splitter.setHandleWidth(1)
        self.setCentralWidget(splitter)

        # Item counts sit on the right; the left is for temporary messages
        self._count_label = QLabel()
        self.statusBar().addPermanentWidget(self._count_label)
        self.statusBar().setSizeGripEnabled(False)

        self._path_bar = PathBar()
        self._path_bar.location_chosen.connect(self.navigate)
        self._path_bar.text_entered.connect(self._on_path_entered)
        self._create_actions()

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
        if self._pending_select:
            self._view.select_name(self._pending_select)
            self._pending_select = None
        self._update_state()

    def _on_load_failed(self, message):
        self._error = message
        self._update_state()

    def _update_state(self, *_args):
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
            selected = self._view.selected_count()
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
        if info is None:
            menu.addSeparator()
            menu.addAction(self._hidden)
        menu.exec(position)

    def closeEvent(self, event):
        self._model.close()
        super().closeEvent(event)
