"""One browser window: toolbar, sidebar, file list, operations panel and status bar."""

from types import SimpleNamespace

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
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
from filehouse.core.listing import mode_text
from filehouse.core.operations import JobKind, permissions, quick
from filehouse.core.operations.conflicts import split_extension
from filehouse.integration import desktop, style
from filehouse.qt_models.directory_model import (
    MODIFIED,
    NAME,
    SIZE,
    DirectoryModel,
    DirectorySortProxy,
)
from filehouse.qt_models.job_model import JobModel
from filehouse.qt_models.permissions import PermissionChange
from filehouse.qt_models.volumes import Volumes
from filehouse.ui import clipboard, menus, settings
from filehouse.ui.details_pane import DetailsPane
from filehouse.ui.dialogs.details import ErrorDetails
from filehouse.ui.dialogs.name import NameDialog
from filehouse.ui.dialogs.prompter import Prompter
from filehouse.ui.dialogs.properties import PropertiesDialog
from filehouse.ui.folder_view import FolderView
from filehouse.ui.job_status import JobStatus
from filehouse.ui.operations_panel import OperationsPanel
from filehouse.ui.path_bar import PathBar
from filehouse.ui.sheet import Sheet
from filehouse.ui.sidebar import Sidebar

_MESSAGE_MS = 8000
_DETAILS_MS = 200
_COPIED_MS = 2500
# A window less wide than this, unscaled, gets the narrow layout: the sidebar as a
# rail of icons, the actions in a bar at the bottom, the details as a sheet
NARROW_BELOW = 800
# The least the narrow layout needs, unscaled: its toolbar side by side, a few rows
_MINIMUM_SIZE = (460, 360)
# What "Sort by" offers, with the order each starts in: the newest and the largest first
_SORTS = (
    ("Name", NAME, Qt.SortOrder.AscendingOrder),
    ("Modified", MODIFIED, Qt.SortOrder.DescendingOrder),
    ("Size", SIZE, Qt.SortOrder.DescendingOrder),
)
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


def pick_folder(parent, title, start):
    """Ask for a folder with the desktop's own dialog. Returns its path, or "" for none."""
    return QFileDialog.getExistingDirectory(parent, title, start)


def quit_app():
    QApplication.quit()


class MainWindow(QMainWindow):
    def __init__(self, location, select=None, message=None, jobs=None):
        """`jobs` is the process's JobModel. A window made without one gets its own."""
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowIcon(QIcon.fromTheme("system-file-manager"))
        self.setMinimumSize(style.px(_MINIMUM_SIZE[0]), style.px(_MINIMUM_SIZE[1]))
        self.resize(settings.window_size(QSize(style.px(1080), style.px(660))))

        self._history = History()
        self._location = None
        self._pending_select = None
        self._error = None
        self._closing_when_idle = False
        self._narrow = False
        self._wide_sizes = None

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
        self._details = DetailsPane()
        self._details.open_requested.connect(self._open_selected)
        # Rows change for many reasons (counts, thumbnails), and often: the pane
        # looks at most a few times a second whether what it shows is one of them
        self._details_timer = QTimer(self)
        self._details_timer.setSingleShot(True)
        self._details_timer.setInterval(_DETAILS_MS)
        self._details_timer.timeout.connect(self._update_details)
        self._proxy.dataChanged.connect(self._update_details_soon)
        # The same details for a narrow window: a sheet that slides up from the bottom
        self._sheet_details = DetailsPane(compact=True)
        self._sheet = Sheet(self._sheet_details)
        # The sheet takes the list's lower part: what is selected stays in view.
        # A moment later, once the list has its new height
        self._sheet.settled.connect(lambda: QTimer.singleShot(0, self._view.show_current))

        self._path_bar = PathBar()
        self._path_bar.location_chosen.connect(self.navigate)
        self._path_bar.text_entered.connect(self._on_path_entered)
        self._create_actions()
        self._sheet_details.close_requested.connect(lambda: self._show_details.setChecked(False))

        splitter = self._splitter = QSplitter()
        splitter.addWidget(self._sidebar)
        splitter.addWidget(self._view)
        splitter.addWidget(self._details)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([style.px(200), style.px(620), style.px(250)])
        splitter.setHandleWidth(style.px(8))
        splitter.setContentsMargins(style.px(8), 0, style.px(8), style.px(4))
        # Only in a narrow window, and only with something selected to act on
        self._action_bar = self._group(
            self._copy,
            self._move_to,
            self._rename,
            self._trash,
            self._show_details,
            self._more,
            self._permissions,
        )
        # The lock stands apart from the rest, behind a line
        line = QFrame()
        line.setObjectName("divider")
        line.setFixedSize(1, style.px(20))
        bar = self._action_bar.layout()
        bar.insertSpacing(bar.count() - 1, style.px(6))
        bar.insertWidget(bar.count() - 1, line)
        bar.insertSpacing(bar.count() - 1, style.px(6))
        bar_row = QHBoxLayout()
        bar_row.setContentsMargins(0, 0, 0, style.px(4))
        bar_row.addStretch(1)
        bar_row.addWidget(self._action_bar)
        bar_row.addStretch(1)
        self._action_bar.hide()
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(splitter, 1)
        layout.addLayout(bar_row)
        layout.addWidget(self._sheet)
        layout.addWidget(self._panel)
        self.setCentralWidget(central)

        # Item counts on the left, where temporary messages take their place for a
        # while; the running operations on the right, which open the panel
        self._count_label = QLabel()
        self.statusBar().addWidget(self._count_label, 1)
        self._job_status = JobStatus(self._jobs)
        self._job_status.clicked.connect(self._panel.toggle)
        self.statusBar().addPermanentWidget(self._job_status)
        self.statusBar().setSizeGripEnabled(False)

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
        self._new_file = action("New file…", "document-new", [], self.new_file)
        self._move_to = action("Move to…", "document-export", [], self.move_selected)
        # The keys Dolphin has for these two
        self._copy_path = action(
            "Copy path", "edit-copy-path", [QKeySequence("Ctrl+Alt+C")], self.copy_path
        )
        self._terminal = action(
            "Open terminal here",
            "utilities-terminal",
            [QKeySequence("Shift+F4")],
            lambda: self.open_terminal(),
        )
        self._select_all = action(
            "Select all",
            "edit-select-all",
            QKeySequence.keyBindings(keys.SelectAll),
            self._view.select_all,
        )
        self._restore = action("Restore", "edit-undo", [], self.restore_selected)
        self._empty_trash = action("Empty trash…", "trash-empty", [], self.empty_trash)
        self._properties = action(
            "Properties", "document-properties", [QKeySequence("Alt+Return")], self.properties
        )

        # For the narrow window. Neither has a key of its own
        self._show_details = action("Details", "documentinfo", [], lambda: None)
        self._show_details.setCheckable(True)
        # Not `triggered`: the sheet's own button and Escape uncheck it too
        self._show_details.toggled.connect(self._set_sheet_open)
        self._close_details = action(
            "Close details",
            "window-close",
            [QKeySequence("Escape")],
            lambda: self._show_details.setChecked(False),
        )
        self._close_details.setEnabled(False)
        self._more = action("More…", "view-more-horizontal", [], self._show_selection_menu)
        self._permissions = action("Permissions", "object-locked", [], self._show_permissions_menu)
        self._folder_menu = action(
            "Folder menu", "view-more-horizontal", [], self._show_folder_menu
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
        # In a narrow window the actions are in a bar at the bottom, and the folder
        # menu has a button
        self._toolbar_actions = toolbar.addWidget(
            self._group(self._copy, self._move_to, self._rename, self._trash)
        )
        toolbar.addWidget(self._group(self._details_mode, self._icons_mode))
        self._toolbar_menu = toolbar.addWidget(self._group(self._folder_menu))
        self._toolbar_menu.setVisible(False)
        self.addToolBar(toolbar)

    def _button_for(self, action):
        """The toolbar or action bar button of an action."""
        return next(
            button for button in self.findChildren(QToolButton) if button.defaultAction() is action
        )

    def _group(self, *actions):
        """A rounded group for the toolbar, holding an icon button per action."""
        group = QFrame()
        group.setObjectName("group")
        layout = QHBoxLayout(group)
        padding = style.px(style.GROUP_PADDING)
        layout.setContentsMargins(padding, padding, padding, padding)
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
        self._move_to.setEnabled(selected > 0 and not in_trash)
        self._permissions.setEnabled(selected > 0 and not in_trash)
        self._paste.setEnabled(bool(clipboard.get()[0]) and not in_trash)
        for item in (self._new_folder, self._new_file):
            item.setEnabled(not in_trash)
        # A terminal starts in a folder of this computer: not in the trash or a remote place
        self._terminal.setVisible(self._terminal_folder() is not None)
        self._restore.setEnabled(
            selected > 0 and all(info.original_path is not None for info in infos)
        )
        self._empty_trash.setEnabled(at_trash and self._model.rowCount() > 0)

        count = self._proxy.rowCount()
        self._select_all.setEnabled(count > 0)
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
            if self._narrow and selected == 1 and infos[0].mode is not None:
                # Short, and with what the narrow list has no column to spare for
                text = f"{infos[0].display_name} · {mode_text(infos[0].mode)}"
        self._count_label.setText(text)
        self._update_narrow_parts(selected)
        self._update_details(infos)

    def _update_details_soon(self, *_args):
        if not self._details_timer.isActive():
            self._details_timer.start()

    def _update_details(self, infos=None):
        if self._location is None:
            return
        if infos is None:
            infos = self._view.selected_infos()
        count = None if self._model.loading or self._error else self._proxy.rowCount()
        if not self._narrow:
            self._details.show_items(self._location, infos, count)
        elif self._sheet.is_open:
            self._sheet_details.show_items(self._location, infos, count)

    # The narrow layout

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._set_narrow(self.width() < style.px(NARROW_BELOW))

    @property
    def is_narrow(self):
        return self._narrow

    def _set_narrow(self, narrow):
        if narrow == self._narrow:
            return
        self._narrow = narrow
        if narrow:
            self._wide_sizes = self._splitter.sizes()
        else:
            self._show_details.setChecked(False)
        self._details.setVisible(not narrow)
        self._sidebar.set_rail(narrow)
        self._path_bar.set_short(narrow)
        self._view.set_compact(narrow)
        self._job_status.set_compact(narrow)
        self._button_for(self._forward).setVisible(not narrow)
        self._toolbar_actions.setVisible(not narrow)
        self._toolbar_menu.setVisible(narrow)
        if narrow:
            # All the width the rail leaves is the list's
            rail = self._sidebar.maximumWidth()
            self._splitter.setSizes([rail, max(1, self._splitter.width() - rail), 0])
        elif self._wide_sizes:
            self._splitter.setSizes(self._wide_sizes)
        self._update_state()

    def _update_narrow_parts(self, selected):
        # The bar acts on the selection, and makes way for the sheet
        self._action_bar.setVisible(self._narrow and selected > 0 and not self._sheet.is_open)

    def _set_sheet_open(self, open_):
        self._sheet.set_open(open_ and self._narrow, animate=self.isVisible())
        self._close_details.setEnabled(self._sheet.is_open)
        self._update_state()

    def _show_folder_menu(self):
        button = self._button_for(self._folder_menu)
        self._on_context_menu(None, button.mapToGlobal(button.rect().bottomLeft()))

    def _show_selection_menu(self):
        infos = self._view.selected_infos()
        if infos:
            button = self._button_for(self._more)
            # The bar is at the bottom of the window: the menu opens upwards
            self._on_context_menu(infos[0], button.mapToGlobal(button.rect().topLeft()), True)

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

    def _open_selected(self):
        infos = self._view.selected_infos()
        if len(infos) == 1:
            self._on_item_activated(infos[0])

    def _show_error(self, message):
        self.statusBar().showMessage(message, _MESSAGE_MS)

    def _on_context_menu(self, info, position, above=False):
        """The menu of an entry, or of the folder for None. `above` puts the menu's
        bottom at `position` instead of its top."""
        menu = self._menu_for(info)
        if above:
            position = QPoint(position.x(), position.y() - menu.sizeHint().height())
        menu.exec(position)

    def _menu_for(self, info):
        menu = menus.menu(self)
        if self._in_trash():
            if info is not None:
                menu.addActions([self._restore, self._delete])
                menu.addSeparator()
                menu.addAction(self._properties)
            else:
                menu.addActions([self._empty_trash, self._select_all])
                menu.addSeparator()
                self._add_sort_and_view(menu)
            return menu
        if info is None:
            # The folder's own menu
            menu.addActions([self._new_folder, self._new_file])
            menu.addSeparator()
            menu.addActions([self._paste, self._select_all])
            menu.addSeparator()
            self._add_sort_and_view(menu)
            menu.addAction(self._hidden)
            menu.addSeparator()
            menu.addActions([self._copy_path, self._terminal])
            self._add_open_in_manager(menu, self._location)
            menu.addAction(self._properties)
            return menu
        target = locations.child(self._location, info.name)
        folder = self._location
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
        self._add_open_in_manager(menu, folder)
        menu.addSeparator()
        menu.addActions([self._cut, self._copy, self._paste])
        menu.addSeparator()
        menu.addActions([self._rename, self._move_to, self._trash, self._delete])
        menu.addSeparator()
        menu.addAction(self._copy_path)
        if info.is_dir and self._terminal_folder(folder) is not None:
            # In the folder the menu is for, not the one it is listed in
            menu.addAction(
                self._terminal.icon(), "Open terminal here", lambda: self.open_terminal(folder)
            )
        menu.addSeparator()
        menu.addMenu(self._permissions_menu(menu))
        menu.addAction(self._properties)
        return menu

    def _permissions_menu(self, parent):
        """The presets, for the selection: each with the mode it gives, and a tick
        on the one the selection has already."""
        menu = menus.menu(parent, "Permissions")
        menu.setIcon(self._permissions.icon())
        infos = self._view.selected_infos()
        kinds = {info.is_dir for info in infos}
        for preset in permissions.PRESETS:
            modes = {preset.mode_for(is_dir) for is_dir in kinds}
            text = preset.label
            if len(modes) == 1 and None not in modes:
                # Files and folders together get different modes: then the name says enough
                text += f"\t{next(iter(modes)):o}"
            item = menu.addAction(text, lambda preset=preset: self.set_permissions(preset))
            # Nothing to do for a preset that leaves every selected item alone
            item.setEnabled(modes != {None})
            item.setCheckable(True)
            item.setChecked(
                bool(infos)
                and all(
                    info.mode is not None and (info.mode & 0o777) == preset.mode_for(info.is_dir)
                    for info in infos
                )
            )
        return menu

    def _show_permissions_menu(self):
        if not self._view.selected_infos() or self._in_trash():
            return
        menu = self._permissions_menu(self)
        button = self._button_for(self._permissions)
        position = button.mapToGlobal(button.rect().topLeft())
        # From the bar at the bottom of the window: upwards
        menu.exec(QPoint(position.x(), position.y() - menu.sizeHint().height()))

    def _add_open_in_manager(self, menu, folder):
        manager = desktop.file_manager()
        if manager is not None:
            menu.addAction(
                style.icon("folder-open-symbolic", "folder-open"),
                f"Open folder in {manager.name}",
                lambda: desktop.open_with(manager, folder, self._show_error),
            )

    def _add_sort_and_view(self, menu):
        """The folder menu's entries for how the list is shown."""
        column, order = self._view.sort_column(), self._view.sort_order()
        sort = menus.menu(menu, "Sort by")
        sort.setIcon(style.icon("view-sort-symbolic", "view-sort"))
        for text, sorts_by, first_order in _SORTS:
            item = sort.addAction(
                text, lambda by=sorts_by, first=first_order: self._view.sort(by, first)
            )
            item.setCheckable(True)
            item.setChecked(sorts_by == column)
        sort.addSeparator()
        for text, way in (
            ("Ascending", Qt.SortOrder.AscendingOrder),
            ("Descending", Qt.SortOrder.DescendingOrder),
        ):
            item = sort.addAction(text, lambda way=way: self._view.sort(column, way))
            item.setCheckable(True)
            item.setChecked(way == order)
        menu.addMenu(sort)
        view = menus.menu(menu, "View")
        view.setIcon(style.icon("view-grid-symbolic", "view-grid"))
        view.addActions([self._details_mode, self._icons_mode])
        menu.addMenu(view)

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

    def set_permissions(self, preset):
        """Give the selection the permissions of `preset`. Only the items themselves:
        nothing inside a selected folder is changed."""
        selected = self._selected_locations()
        if not selected or self._in_trash():
            return None
        name = self._view.selected_infos()[0].display_name
        change = PermissionChange(preset, selected, self)
        change.done.connect(lambda outcome: self._on_permissions_set(outcome, len(selected), name))
        change.done.connect(change.deleteLater)
        return change

    def _on_permissions_set(self, outcome, count, name):
        what = f"“{name}”" if count == 1 else f"{count} items"
        failed = len(outcome.errors)
        if not failed:
            text = f"Permissions of {what} changed"
            if outcome.skipped:
                # "Make executable" on a selection with folders in it
                changed = "1 file" if outcome.changed == 1 else f"{outcome.changed} files"
                text = f"Permissions of {changed} changed, folders left as they were"
            self.statusBar().showMessage(text, _COPIED_MS)
        elif failed == 1:
            self._show_error(f"Could not change the permissions of {outcome.errors[0]}.")
        else:
            # Every one of them, by name
            summary = f"Could not change the permissions of {failed} of {count} items."
            self._show_error(summary)
            ErrorDetails(SimpleNamespace(errors=outcome.errors, summary=summary), self).open()

    def copy_path(self):
        """Put the paths of the selection on the clipboard as text, one per line.
        With nothing selected, the path of this folder."""
        selected = self._selected_locations() or [self._location]
        clipboard.put_text("\n".join(locations.display_path(item) for item in selected))
        what = "Path" if len(selected) == 1 else f"{len(selected)} paths"
        self.statusBar().showMessage(f"{what} copied", _COPIED_MS)

    def _terminal_folder(self, folder=None):
        """The path a terminal would start in, or None when it can't: no terminal
        installed, or a folder that is not on this computer's file systems."""
        folder = folder or self._location
        if folder is None or desktop.default_terminal() is None:
            return None
        return locations.local_path(folder)

    def open_terminal(self, folder=None):
        path = self._terminal_folder(folder)
        if path is not None:
            desktop.open_terminal(path, self._show_error)

    def move_selected(self):
        """Move the selection to a folder that is picked in a dialog."""
        selected = self._selected_locations()
        if not selected or self._in_trash():
            return None
        start = locations.local_path(self._location) or locations.local_path(locations.home())
        path = pick_folder(self, "Move to", start)
        if not path:
            return None
        target = locations.from_path(path)
        if self._all_in(selected, target):
            # Already there
            return None
        return self._start(JobKind.MOVE, selected, target)

    def new_file(self):
        if self._in_trash():
            return None
        folder = self._location
        name = quick.suggest_file_name(folder)

        def create(text):
            try:
                made = quick.create_file(folder, text)
            except OperationError as error:
                return f"Could not create the file: {error.message}."
            self._select_when_listed(folder, locations.basename(made))
            return None

        return self._ask_name("New file", "Name of the new file:", name, create)

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

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            # Folders may have been filled or emptied by another app meanwhile
            self._model.recount()

    def _on_jobs_idle(self):
        self._model.recount()
        # What a job copied or deleted shows in how full the drives are
        self._volumes.refresh_usage()
        if self._closing_when_idle:
            self._closing_when_idle = False
            self.close()
            if not self._other_windows_open():
                # Closing a hidden window doesn't end the app by itself
                QTimer.singleShot(0, quit_app)
