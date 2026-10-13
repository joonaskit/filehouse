"""One browser window: toolbar, sidebar, file list, operations panel and status bar."""

from types import SimpleNamespace

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QCursor, QIcon, QKeyEvent, QKeySequence
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
    QStackedWidget,
    QTabBar,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from filehouse.core import archives, locations, search
from filehouse.core.errors import OperationError
from filehouse.core.listing import mode_text
from filehouse.core.operations import JobKind, permissions, quick
from filehouse.core.operations.conflicts import split_extension
from filehouse.integration import desktop, style
from filehouse.qt_models.directory_model import MODIFIED, NAME, SIZE
from filehouse.qt_models.job_model import JobModel
from filehouse.qt_models.permissions import PermissionChange
from filehouse.qt_models.volumes import Volumes
from filehouse.ui import clipboard, menus, settings
from filehouse.ui.details_pane import DetailsPane
from filehouse.ui.dialogs.compress import CompressDialog
from filehouse.ui.dialogs.details import ErrorDetails
from filehouse.ui.dialogs.name import NameDialog
from filehouse.ui.dialogs.prompter import Prompter
from filehouse.ui.dialogs.properties import PropertiesDialog
from filehouse.ui.dialogs.rename_many import RenameManyDialog
from filehouse.ui.dialogs.select import SelectDialog
from filehouse.ui.dialogs.settings import SettingsDialog
from filehouse.ui.job_status import JobStatus
from filehouse.ui.operations_panel import OperationsPanel
from filehouse.ui.pane import Pane
from filehouse.ui.path_bar import PathBar
from filehouse.ui.preview import SHEET_SHARE, PreviewWindow
from filehouse.ui.search_field import SearchField
from filehouse.ui.sheet import Sheet
from filehouse.ui.sidebar import Sidebar
from filehouse.ui.tab import Tab
from filehouse.ui.tab_bar import TabBar

# Width of the search field in a wide window, unscaled
_SEARCH_WIDTH = 220
_SEARCH_LEAST = 120
_NO_LIMIT = 16777215
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
# What a drop can be asked to do beside the two kinds of job
LINK = "link"


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


def ask_to_clear_recent(parent, count=None):
    """Whether to empty the list of recent files. It is shared with other apps, so it asks.
    Without a `count` the question does not say how many."""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle("Clear the recent files?")
    box.setText(
        "Remove all files from the list of recent files?"
        if count is None
        else "Remove the one file from the list of recent files?"
        if count == 1
        else f"Remove all {count} files from the list of recent files?"
    )
    box.setInformativeText(
        "The files themselves are not touched. Plasma's own history is cleared in "
        "System Settings, under Recent Files."
    )
    clear = box.addButton("Clear", QMessageBox.ButtonRole.DestructiveRole)
    cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.setEscapeButton(cancel)
    box.exec()
    return box.clickedButton() is clear


def pick_folder(parent, title, start):
    """Ask for a folder with the desktop's own dialog. Returns its path, or "" for none."""
    return QFileDialog.getExistingDirectory(parent, title, start)


def quit_app():
    QApplication.quit()


def session_to_open():
    """The tabs of last time that can be opened now: a list of (the one or two folders
    of a tab, which of them is the active one) and the tab in front, or None. A folder
    that is gone is the nearest one above it that is there, and left out when none is."""
    session = settings.session() if settings.restore_tabs() else None
    if session is None:
        return None
    tabs, front = session
    opened = []
    in_front = 0
    for index, (uris, active) in enumerate(tabs):
        folders = [locations.nearest_folder(locations.from_uri(uri)) for uri in uris]
        if all(folder is None for folder in folders):
            continue
        if folders[0] is None or len(folders) == 1 or folders[1] is None:
            # One of a split is gone: the tab is the other
            folders = [next(folder for folder in folders if folder is not None)]
        if index == front:
            in_front = len(opened)
        opened.append((folders, active if 0 <= active < len(folders) else 0))
    return (opened, in_front) if opened else None


class MainWindow(QMainWindow):
    def __init__(
        self,
        location,
        select=None,
        message=None,
        jobs=None,
        recent=None,
        favourites=None,
        session=None,
    ):
        """`jobs` is the process's JobModel. A window made without one gets its own.
        `recent` records the files opened from the window for the desktop's lists of
        recent files; without one nothing is recorded. `favourites` is the process's list
        of favourite folders, shown in the sidebar; without one there is no such group.
        `session` is what `session_to_open` gave: the tabs to open, `location` being
        the first of them."""
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowIcon(QIcon.fromTheme("system-file-manager"))
        self.setMinimumSize(style.px(_MINIMUM_SIZE[0]), style.px(_MINIMUM_SIZE[1]))
        self.resize(settings.window_size(QSize(style.px(1080), style.px(660))))

        self._closing_when_idle = False
        self._narrow = False
        self._wide_sizes = None

        if jobs is None:
            jobs = JobModel(self)
            self._prompter = Prompter(jobs, self)
        self._jobs = jobs
        self._recent = recent
        self._favourites = favourites
        self._jobs.idle.connect(self._on_jobs_idle)

        # Tabs, each with a folder or two side by side. `_tab` is the shown one and
        # `_pane` its active pane: the toolbar, the title, the details, the status bar
        # and every action are about it
        self._action_icons = []  # (action, icon name, colour) to make them again
        self._button_actions = []  # the ones a button shows, not only a menu
        self._unsplit_sizes = None
        self._tab_bar = TabBar()
        self._tab_bar.currentChanged.connect(self._on_tab_changed)
        self._tab_bar.tabMoved.connect(self._on_tab_moved)
        self._tab_bar.dropped.connect(self._on_tab_dropped)
        self._tab_bar.setVisible(False)
        self._stack = QStackedWidget()
        self._tabs = []
        self._tab = self._add_tab()

        self._volumes = Volumes(self)
        self._volumes.removed.connect(self._on_volume_removed)
        self._volumes.failed.connect(self._show_error)
        self._sidebar = Sidebar(locations.places(), self._volumes, self._favourites)
        self._sidebar.place_activated.connect(self.navigate)
        self._sidebar.new_tab_requested.connect(lambda place: self.new_tab(place, activate=False))
        self._sidebar.favourite_menu_requested.connect(self._show_favourite_menu)
        self._sidebar.files_dropped.connect(self._on_dropped_on_place)
        self._sidebar.folders_dropped.connect(self._add_dropped_folders)
        self._panel = OperationsPanel(self._jobs)
        self._details = DetailsPane()
        self._details.open_requested.connect(self._open_selected)
        # Rows change for many reasons (counts, thumbnails), and often: the pane
        # looks at most a few times a second whether what it shows is one of them
        self._details_timer = QTimer(self)
        self._details_timer.setSingleShot(True)
        self._details_timer.setInterval(_DETAILS_MS)
        self._details_timer.timeout.connect(self._update_details)
        # The same details for a narrow window: a sheet that slides up from the bottom
        self._sheet_details = DetailsPane(compact=True)
        self._sheet = Sheet(self._sheet_details)
        self._preview = PreviewWindow(self)
        self._preview.navigate.connect(self._on_preview_key)
        self._preview.shown_changed.connect(lambda _shown: self._update_sheet_actions())
        # The same preview for a narrow window: a sheet too, in place of the details'
        self._sheet_preview = PreviewWindow(self, sheet=True)
        self._preview_sheet = Sheet(self._sheet_preview)
        self._sheet_preview.close_requested.connect(self._close_preview)
        self._sheet_preview.full_screen_requested.connect(self._preview_full_screen)
        self._sheet_preview.pages_changed.connect(lambda _count: self._update_sheet_actions())
        self._preview_sheet.settled.connect(lambda: QTimer.singleShot(0, self._view.show_current))
        # The sheet takes the list's lower part: what is selected stays in view.
        # A moment later, once the list has its new height
        self._sheet.settled.connect(lambda: QTimer.singleShot(0, self._view.show_current))

        self._path_bar = PathBar()
        self._path_bar.location_chosen.connect(self.navigate)
        self._path_bar.text_entered.connect(self._on_path_entered)
        self._search_field = SearchField()
        self._search_field.textChanged.connect(self._on_search_text)
        self._search_field.cancelled.connect(self.end_search)
        self._search_field.left.connect(self._to_the_list)
        self._search_field.editingFinished.connect(self._sync_search_field)
        self._create_actions()
        self._sheet_details.close_requested.connect(lambda: self._show_details.setChecked(False))

        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(0)
        center_layout.addWidget(self._tab_bar)
        center_layout.addWidget(self._stack, 1)

        splitter = self._splitter = QSplitter()
        splitter.addWidget(self._sidebar)
        splitter.addWidget(center)
        splitter.addWidget(self._details)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([style.px(200), style.px(620), style.px(250)])
        splitter.setHandleWidth(style.px(8))
        splitter.setContentsMargins(style.px(8), 0, style.px(8), style.px(4))
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(splitter, 1)
        layout.addWidget(self._sheet)
        layout.addWidget(self._preview_sheet)
        layout.addWidget(self._panel)
        self.setCentralWidget(central)

        # Item counts on the left, where temporary messages take their place for a
        # while; the running operations on the right, which open the panel
        self._count_label = QLabel()
        # In a narrow window, in the corner under the rail: the details as a sheet
        self._details_button = QToolButton()
        self._details_button.setDefaultAction(self._show_details)
        self._button_actions.append(self._show_details)
        self._details_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self._details_button.setIconSize(QSize(style.px(18), style.px(18)))
        self._details_button.setFixedHeight(style.px(28))
        self._details_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._details_button.setVisible(False)
        self.statusBar().addWidget(self._details_button)
        self.statusBar().addWidget(self._count_label, 1)
        self._job_status = JobStatus(self._jobs)
        self._job_status.clicked.connect(self._panel.toggle)
        # A sign that nothing is remembered, as long as it is so: a click ends it
        self._private_button = QToolButton()
        self._button_actions.append(self._private)
        self._draw_action_icons()
        self._private_button.setText("Private")
        self._private_button.clicked.connect(self._private.trigger)
        self._private_button.setToolTip(
            "Opened files are not remembered. Click to remember them again."
        )
        self._private_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._private_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._private_button.setIconSize(QSize(style.px(14), style.px(14)))
        self._private_button.setVisible(not settings.remember_opened())
        settings.changes.remember_opened_changed.connect(self._on_remember_opened_changed)
        self.statusBar().addPermanentWidget(self._private_button)
        self.statusBar().addPermanentWidget(self._job_status)
        self.statusBar().setSizeGripEnabled(False)

        QApplication.clipboard().dataChanged.connect(self._update_state)
        style.themed.changed.connect(self._retheme)

        # A window opens without the pane when the user has put it away
        self._sync_details()
        if session is not None and self.width() < style.px(NARROW_BELOW):
            # No room for a split: the first tab is the pane of it that was active
            first, active = session[0][0]
            location = first[active]
        self.navigate(location, select=select)
        if session is not None:
            self._open_session(*session)
        elif settings.split() and self.width() >= style.px(NARROW_BELOW):
            self.set_split(True, remember=False)
        if message:
            self.statusBar().showMessage(message, _MESSAGE_MS)
        self._view.setFocus()

    def _create_actions(self):
        def action(text, icon, shortcuts, slot, colour="textPrimary"):
            # Its icon comes with all the others, once it is known which are on buttons
            item = QAction(text, self)
            item.setShortcuts(shortcuts)
            item.triggered.connect(slot)
            self.addAction(item)
            self._action_icons.append((item, icon, colour))
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
        # The tab, or the window when it is the last tab
        action("Close", "window-close", [QKeySequence("Ctrl+W")], self.close_current)
        self._new_tab = action("New tab", "tab-new", [QKeySequence("Ctrl+T")], self.new_tab)
        action(
            "Next tab",
            "go-next",
            [QKeySequence("Ctrl+Tab"), QKeySequence("Ctrl+PgDown")],
            lambda: self._step_tab(1),
        )
        action(
            "Previous tab",
            "go-previous",
            [QKeySequence("Ctrl+Shift+Tab"), QKeySequence("Ctrl+PgUp")],
            lambda: self._step_tab(-1),
        )
        self._hidden = action(
            "Show hidden files",
            "view-hidden",
            [QKeySequence("Ctrl+H")],
            self._set_show_hidden,
        )
        self._hidden.setCheckable(True)
        self._hidden.setChecked(self._proxy.show_hidden)
        # For the user who does not want the desktop to follow what they open. A setting
        # of the app, not of a window, as the browsers' private mode is not either
        self._private = action(
            "Don't remember opened files",
            "view-private",
            [],
            self._set_remember_opened,
        )
        self._private.setCheckable(True)
        self._private.setChecked(not settings.remember_opened())

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
        self._split_view = action(
            "Split view", "view-split-left-right", [QKeySequence("F3")], self.set_split
        )
        self._split_view.setCheckable(True)
        # Whether a wide window has the pane of details. The narrow window's details are
        # a sheet, and have a button of their own
        self._pane_toggle = action(
            "Details pane", "view-right-close", [QKeySequence("F11")], self.set_details_pane
        )
        self._pane_toggle.setCheckable(True)
        self._pane_toggle.setChecked(settings.details_pane())
        settings.changes.details_pane_changed.connect(self._on_details_pane_changed)
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
        # A copy beside the file, and a link to it. Neither has a key: Ctrl+D is the
        # favourites', and Dolphin has none for them either
        self._duplicate = action("Duplicate", "edit-duplicate", [], self.duplicate)
        self._link = action("Create link", "edit-link", [], self.create_link)
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
        # Archives: nothing is offered when libarchive is not installed
        self._extract_here = action("Extract here", "archive-extract", [], self.extract_here)
        self._extract_to = action("Extract to…", "archive-extract", [], self.extract_to)
        self._compress = action("Compress…", "archive-insert", [], self.compress)
        # Beside the other list, for the keyboard. Shift: F5 is Reload
        self._copy_other = action(
            "Copy to other pane", "edit-copy", [QKeySequence("Shift+F5")], self.copy_to_other_pane
        )
        self._move_other = action(
            "Move to other pane",
            "document-export",
            [QKeySequence("Shift+F6")],
            self.move_to_other_pane,
        )
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
            lambda: self._view.select_all(),
        )
        # The keys Dolphin has for these two
        self._select_matching = action(
            "Select matching…", "edit-select", [QKeySequence("Ctrl+S")], self.select_matching
        )
        self._invert = action(
            "Invert selection",
            "edit-select-invert",
            [QKeySequence("Ctrl+Shift+A")],
            self.invert_selection,
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
        # Space opens a larger look at the selection, and Space or Escape close it
        self._preview_action = action(
            "Preview", "view-preview", [QKeySequence("Space")], self.toggle_preview
        )
        # For the preview as a sheet, which takes no keys itself. Only while it is open,
        # and the page keys only for a document: they are the list's otherwise
        self._close_preview_sheet = action(
            "Close preview", "window-close", [QKeySequence("Escape")], self._close_preview
        )
        self._next_page = action(
            "Next page", "go-down", [QKeySequence("PgDown")], lambda: self._turn_page(1)
        )
        self._previous_page = action(
            "Previous page", "go-up", [QKeySequence("PgUp")], lambda: self._turn_page(-1)
        )
        for item in (self._close_preview_sheet, self._next_page, self._previous_page):
            item.setEnabled(False)
        # In the recent files the Delete key takes the entry off the list
        self._forget = action(
            "Remove from recent files",
            "edit-clear-history",
            [QKeySequence("Delete")],
            self.remove_from_recent,
        )
        self._forget.setEnabled(False)
        self._clear_recent = action(
            "Clear recent files…", "edit-clear-history", [], self.clear_recent
        )
        # The folder open here, or the one a menu is for, in the bar under the toolbar
        self._favourite = action(
            "Add to favourites", "bookmark-new", [QKeySequence("Ctrl+D")], self.toggle_favourite
        )
        self._permissions = action("Permissions", "object-locked", [], self._show_permissions_menu)
        self._settings = action(
            "Settings", "preferences-system", [QKeySequence("Ctrl+,")], self.open_settings
        )
        self._folder_menu = action(
            "Folder menu", "view-more-horizontal", [], self._show_folder_menu
        )
        self._search = action("Search", "edit-find", [QKeySequence("Ctrl+F")], self.start_search)

        # One row of rounded groups, over the panes they go with: back and forward over
        # the sidebar, the path and how the list is shown over the list, and search over
        # the details. What is done to the selection is in its menu, and in the bar at
        # the bottom of a narrow window
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        # The first group is as wide as the sidebar under it, and the search field with
        # the settings button as wide as the details pane: see _align_toolbar
        self._navigation_group = self._group(self._back, self._forward, self._up)
        self._navigation_group.layout().addStretch(1)
        toolbar.addWidget(self._navigation_group)
        location = self._group()
        location.layout().addWidget(self._path_bar)
        location.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._toolbar_location = toolbar.addWidget(location)
        self._view_group = self._group(
            self._details_mode, self._icons_mode, self._split_view, self._pane_toggle
        )
        toolbar.addWidget(self._view_group)
        # In a wide window the field is always there; in a narrow one it takes the
        # place of the path while it is in use, and a button opens it
        self._search_group = self._group()
        self._search_group.layout().addWidget(self._search_field)
        self._search_group.setFixedWidth(style.px(_SEARCH_WIDTH))
        self._toolbar_search = toolbar.addWidget(self._search_group)
        self._toolbar_menu = toolbar.addWidget(self._group(self._search, self._folder_menu))
        self._toolbar_menu.setVisible(False)
        self._settings_group = self._group(self._settings)
        toolbar.addWidget(self._settings_group)
        self._toolbar = toolbar
        self.addToolBar(toolbar)
        for pane in (self._sidebar, self._details):
            pane.installEventFilter(self)
        if self._favourites is not None:
            self._favourites.changed.connect(self._update_favourite_action)

    def _draw_action_icons(self):
        """The icon of every action, in the colours of the theme in use. Most are shown
        by menus only and are made in a menu's size: every size of every icon was most
        of the time the window took to open."""
        for item, icon, colour in self._action_icons:
            menu = item not in self._button_actions
            item.setIcon(style.icon(icon + "-symbolic", icon, colour=colour, menu=menu))
        self._private_button.setIcon(self._private.icon())

    def _retheme(self):
        """The theme changed while the window is open: icons are drawn in its colours."""
        self._draw_action_icons()
        for index in range(self._tab_bar.count()):
            button = self._tab_bar.tabButton(index, QTabBar.ButtonPosition.RightSide)
            button.setIcon(style.icon("window-close-symbolic", "window-close"))
        self._sidebar.retheme()
        self._details.retheme()
        self._sheet_details.retheme()
        self._sheet_preview.retheme()
        self._search_field.retheme()

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
        padding = style.px(style.dense("group_padding"))
        layout.setContentsMargins(padding, padding, padding, padding)
        layout.setSpacing(style.px(2))
        for item in actions:
            button = QToolButton()
            button.setDefaultAction(item)
            self._button_actions.append(item)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            button.setIconSize(QSize(style.px(18), style.px(18)))
            side = style.px(style.dense("control_height"))
            button.setFixedSize(side, side)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            layout.addWidget(button)
        return group

    def eventFilter(self, watched, event):
        if watched in (self._sidebar, self._details) and event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.Hide,
        ):
            # Once the pane has its place
            QTimer.singleShot(0, self._align_toolbar)
        return super().eventFilter(watched, event)

    def _align_toolbar(self):
        """Make the toolbar's groups end where the panes under them do: the first group
        is as wide as the sidebar, so the path starts where the list does, and the
        search field with the settings button is as wide as the details pane. A pane
        that is not there, or a toolbar without the room, leaves a group the width it
        has by itself; a narrow window, whose sidebar is a rail, lays itself out."""
        if self._narrow:
            self._navigation_group.setFixedWidth(self._navigation_group.sizeHint().width())
            self._relay_toolbar()
            # The details button of the status bar: in the middle under the rail
            start = self._details_button.mapTo(self, QPoint(0, 0)).x()
            middle = self._sidebar.mapTo(self, QPoint(0, 0)).x() + self._sidebar.width() / 2
            self._details_button.setFixedWidth(max(style.px(28), round(2 * (middle - start))))
            return
        navigation, search = self._navigation_group, self._search_group
        # The least each can do with, and what it has when nothing is under it
        least = {navigation: navigation.sizeHint().width(), search: style.px(_SEARCH_LEAST)}
        wanted = {navigation: least[navigation], search: style.px(_SEARCH_WIDTH)}
        # The toolbar's padding is its layout's. From where the panes are, not from
        # their widths: the toolbar's padding and the panes' margin need not be the same
        margins = self._toolbar.layout().contentsMargins()
        spacing = self._toolbar.layout().spacing()
        if self._sidebar.isVisible():
            right = self._sidebar.mapTo(self, QPoint(0, 0)).x() + self._sidebar.width()
            wanted[navigation] = max(least[navigation], right - margins.left())
        if self._details.isVisible():
            left = self._details.mapTo(self, QPoint(0, 0)).x()
            beside = self._settings_group.sizeHint().width() + spacing
            end = self._toolbar.width() - margins.right()
            wanted[search] = max(least[search], end - left - beside)
        # What the toolbar has left when every group has the least it needs
        shown = [
            self._toolbar.widgetForAction(action)
            for action in self._toolbar.actions()
            if action.isVisible()
        ]
        needed = sum(
            least[group] if group in least else group.minimumSizeHint().width()
            for group in shown
            if group is not None
        )
        spare = (
            self._toolbar.width()
            - margins.left()
            - margins.right()
            - spacing * (len(shown) - 1)
            - needed
        )
        # The sidebar's edge first: it is the one beside the path
        for group in (navigation, search):
            extra = min(wanted[group] - least[group], max(0, spare))
            spare -= extra
            group.setFixedWidth(least[group] + extra)
        self._relay_toolbar()

    def _relay_toolbar(self):
        # The toolbar keeps the sizes of its widgets until it is told they changed
        self._toolbar.layout().invalidate()
        self._toolbar.layout().activate()

    # The active pane

    @property
    def _view(self):
        return self._pane.view

    @property
    def _model(self):
        return self._pane.model

    @property
    def _proxy(self):
        return self._pane.proxy

    @property
    def _location(self):
        return self._pane.location

    @property
    def _error(self):
        return self._pane.error

    @property
    def _panes(self):
        return self._tab.panes

    @property
    def _pane(self):
        return self._tab.active

    @_pane.setter
    def _pane(self, pane):
        self._tab.active = pane

    @property
    def is_split(self):
        return self._tab.is_split

    def _all_panes(self):
        return [pane for tab in self._tabs for pane in tab.panes]

    def _add_pane(self, tab):
        pane = Pane(settings.show_hidden(), self)
        view = pane.view

        def for_this_pane(handler):
            """A click, a drop or a menu in a list makes it the active one first."""

            def call(*args):
                self._activate(pane)
                return handler(*args)

            return call

        pane.changed.connect(lambda: self._on_pane_changed(pane))
        pane.location_changed.connect(lambda _location: self._on_pane_moved(pane))
        pane.proxy.dataChanged.connect(self._update_details_soon)
        view.focused.connect(lambda: self._activate(pane))
        view.item_activated.connect(for_this_pane(self._on_item_activated))
        view.context_menu_requested.connect(for_this_pane(self._on_context_menu))
        view.dropped.connect(for_this_pane(self._on_dropped))
        view.open_in_tab.connect(for_this_pane(self._open_in_new_tab))
        view.set_compact(self._narrow)
        if tab.active is None:
            tab.active = pane
        tab.panes.append(pane)
        tab.splitter.addWidget(view)
        return pane

    def _other_pane(self):
        return next((pane for pane in self._panes if pane is not self._pane), None)

    def _activate(self, pane):
        if pane is self._pane:
            return
        self._pane = pane
        self._mark_active()
        self._on_location_changed()

    def _mark_active(self, tab=None):
        tab = tab or self._tab
        for pane in tab.panes:
            pane.view.set_split(tab.is_split, pane is tab.active)

    def _on_pane_changed(self, pane):
        if pane is self._pane:
            self._update_state()
        else:
            pane.sync()

    def _on_pane_moved(self, pane):
        self._update_tab_titles()
        if pane is self._pane:
            self._on_location_changed()

    def set_split(self, on=True, remember=True, location=None):
        """Show two folders side by side, or one again: the active pane stays. The new
        pane shows `location`, or by default the folder of the active one."""
        if on == self.is_split or (on and self._narrow):
            self._split_view.setChecked(self.is_split)
            return
        if remember:
            settings.set_split(on)
        if on:
            pane = self._add_pane(self._tab)
            pane.navigate(location or self._pane.location)
            half = self._tab.splitter.width() // 2
            self._tab.splitter.setSizes([half, half])
        else:
            self._remove_other_pane(self._tab)
        self._mark_active()
        self._split_view.setChecked(on)
        self._sync_details()
        self._update_state()
        self._view.setFocus()

    @staticmethod
    def _remove_other_pane(tab):
        gone = next(pane for pane in tab.panes if pane is not tab.active)
        tab.panes.remove(gone)
        gone.view.setParent(None)
        gone.view.deleteLater()
        gone.close()
        gone.deleteLater()

    def _details_wanted(self):
        return not self._narrow and not self.is_split and self._pane_toggle.isChecked()

    def set_details_pane(self, show=None):
        """Show or hide the pane of details in a wide window. It is the app's setting,
        the same in every window; in a narrow or a split window it is kept for later."""
        if show is None:
            show = not settings.details_pane()
        if show != settings.details_pane():
            settings.set_details_pane(show)

    def _on_details_pane_changed(self, show):
        """Changed here or in another window: it is one setting."""
        # The action is checked already when it was its own click that changed it
        self._pane_toggle.setChecked(show)
        self._sync_details()
        self._update_state()

    def _sync_details(self):
        """No room for the details beside two lists: they come and go with the split."""
        wanted = self._details_wanted()
        if wanted == (not self._details.isHidden()):
            return
        if wanted:
            self._details.setVisible(True)
            if self._unsplit_sizes:
                self._splitter.setSizes(self._unsplit_sizes)
        else:
            self._unsplit_sizes = self._splitter.sizes()
            self._details.setVisible(False)

    # Tabs

    def _add_tab(self):
        """A tab with one pane, at the end of the bar. The first one is the window's."""
        tab = Tab()
        self._tabs.append(tab)
        self._add_pane(tab)
        self._stack.addWidget(tab.splitter)
        self._tab_bar.blockSignals(True)
        index = self._tab_bar.addTab("")
        self._tab_bar.blockSignals(False)
        close = QToolButton()
        close.setIcon(style.icon("window-close-symbolic", "window-close"))
        close.setIconSize(QSize(style.px(12), style.px(12)))
        close.setFixedSize(style.px(20), style.px(20))
        close.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close.setToolTip("Close tab")
        close.clicked.connect(lambda: self.close_tab(self._tabs.index(tab)))
        self._tab_bar.setTabButton(index, QTabBar.ButtonPosition.RightSide, close)
        if len(self._tabs) == 1:
            self._stack.setCurrentWidget(tab.splitter)
        self._tab_bar.setVisible(len(self._tabs) > 1)
        return tab

    def new_tab(self, location=None, activate=True):
        """Open a folder in a tab of its own, by default this one's folder."""
        location = location or self._pane.location
        tab = self._add_tab()
        tab.active.navigate(location)
        self._update_tab_titles()
        if activate:
            self._tab_bar.setCurrentIndex(self._tabs.index(tab))
        return tab

    def _open_in_new_tab(self, info):
        """A folder of the list, in a tab behind this one."""
        self.new_tab(locations.entry_location(self._location, info), activate=False)

    def _open_in_split(self, info):
        """A folder of the list, in the other pane."""
        self._open_location_in_split(locations.entry_location(self._location, info))

    def _open_location_in_split(self, target):
        """A folder in the other pane: the window is split first if it is not. The pane
        the choice was made in stays the active one. It is not a choice of how new
        windows open, as F3 is."""
        if self._narrow:
            return
        if not self.is_split:
            self.set_split(True, remember=False)
        other = self._other_pane()
        if other is not None:
            other.navigate(target)

    def close_tab(self, index):
        if len(self._tabs) < 2 or not 0 <= index < len(self._tabs):
            return
        tab = self._tabs.pop(index)
        self._tab_bar.removeTab(index)
        self._stack.removeWidget(tab.splitter)
        for pane in tab.panes:
            pane.close()
            pane.deleteLater()
        tab.splitter.deleteLater()
        self._tab_bar.setVisible(len(self._tabs) > 1)
        if tab is self._tab:
            # The bar has picked the next one: show it
            self._on_tab_changed(self._tab_bar.currentIndex())

    def close_current(self):
        if len(self._tabs) > 1:
            self.close_tab(self._tabs.index(self._tab))
        else:
            self.close()

    def _step_tab(self, step):
        count = len(self._tabs)
        if count > 1:
            self._tab_bar.setCurrentIndex((self._tab_bar.currentIndex() + step) % count)

    def _on_tab_changed(self, index):
        if not 0 <= index < len(self._tabs) or self._tabs[index] is self._tab:
            return
        self._tab = self._tabs[index]
        self._stack.setCurrentWidget(self._tab.splitter)
        self._mark_active()
        self._split_view.setChecked(self.is_split)
        self._sync_details()
        self._on_location_changed()
        self._view.setFocus()

    def _on_tab_moved(self, source, target):
        self._tabs.insert(target, self._tabs.pop(source))

    def _on_tab_dropped(self, index, uris, modifiers, position):
        """Files were dropped on a tab: into the folder it shows."""
        self._drop_on(self._tabs[index].active, uris, None, modifiers, position)

    def _update_tab_titles(self):
        for index, tab in enumerate(self._tabs):
            location = tab.active.location
            if location is not None:
                self._tab_bar.setTabText(index, locations.display_name(location))
                self._tab_bar.setTabToolTip(index, locations.display_path(location))

    # What is remembered

    def set_view_mode(self, mode):
        """Show the open folder as details or as icons. Where folders keep their view
        it is this folder's, and otherwise every folder's: the panes follow the
        settings, this one too."""
        self._pane.choose_mode(mode)
        self._sync_view_mode()

    def _sync_view_mode(self):
        """The two buttons say how the active pane shows its folder."""
        (self._icons_mode if self._view.mode == ICONS else self._details_mode).setChecked(True)

    # The tabs of last time

    def session(self):
        """The tabs as they are now, for `settings.set_session`."""
        tabs = [
            (
                [locations.uri(pane.location) for pane in tab.panes],
                tab.panes.index(tab.active),
            )
            for tab in self._tabs
        ]
        return tabs, self._tabs.index(self._tab)

    def _open_session(self, tabs, front):
        """Open the tabs of last time: the first is the one this window was made with."""
        wide = self.width() >= style.px(NARROW_BELOW)
        for index, (folders, active) in enumerate(tabs):
            if not wide:
                # No room for two lists: the one that was active
                folders, active = [folders[active]], 0
            if index:
                self.new_tab(folders[0])
            else:
                self.navigate(folders[0])
            if len(folders) > 1:
                self.set_split(True, remember=False, location=folders[1])
                if active == 1:
                    self._activate(self._other_pane())
        self._tab_bar.setCurrentIndex(front)

    def set_show_hidden(self, show):
        self._hidden.setChecked(show)
        self._set_show_hidden(show)

    def _set_show_hidden(self, show):
        for pane in self._all_panes():
            pane.proxy.set_show_hidden(show)
        settings.set_show_hidden(show)

    # Navigation

    def navigate(self, location, select=None):
        """Go to a folder and record it in the history."""
        self._pane.navigate(location, select)

    def go_back(self):
        self._pane.go_back()

    def go_forward(self):
        self._pane.go_forward()

    def go_up(self):
        self._pane.go_up()

    def reload(self):
        self._pane.reload()

    def _on_location_changed(self):
        location = self._pane.location
        self.setWindowTitle(f"{locations.display_name(location)} — Files")
        self._path_bar.set_location(locations.segments(location), locations.display_path(location))
        self._sidebar.select(location)
        self._back.setEnabled(self._pane.history.can_go_back)
        self._forward.setEnabled(self._pane.history.can_go_forward)
        self._up.setEnabled(locations.parent(location) is not None)
        self._update_state()

    def _on_volume_removed(self, root):
        """A drive went away. A window that was looking at it has nothing left to show."""
        gone = [
            pane
            for pane in self._all_panes()
            if locations.same(pane.location, root) or locations.inside(pane.location, root)
        ]
        for pane in gone:
            pane.navigate(locations.home())
        if gone:
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

    def _update_state(self, *_args):
        self._pane.sync()
        self._sync_view_mode()
        infos = self._view.selected_infos()
        selected = len(infos)
        in_trash = self._in_trash()
        at_trash = self._at_trash()
        # The trash and the recent files are for looking at, and for what is said below
        read_only = self._read_only()
        self._copy.setEnabled(selected > 0)
        self._properties.setEnabled(True)
        # What is in the trash can be restored or deleted, and nothing else
        self._cut.setEnabled(selected > 0 and not read_only)
        # In the trash the Delete key deletes for good (and asks first, as always)
        for item in (self._trash, self._delete):
            item.setEnabled(selected > 0 and (at_trash or not read_only))
        # In the recent files it takes the entry off the list, not the file away
        self._forget.setEnabled(selected > 0 and self._in_recent())
        self._clear_recent.setEnabled(self._in_recent() and self._model.rowCount() > 0)
        self._update_favourite_action()
        self._rename.setEnabled(selected > 0 and not read_only)
        self._move_to.setEnabled(selected > 0 and not read_only)
        # Not of what is in the trash, and not into a list of what was used
        for item in (self._duplicate, self._link):
            item.setEnabled(selected > 0 and not read_only)
        has_archive = not in_trash and any(self._is_archive(info) for info in infos)
        self._extract_here.setEnabled(has_archive and not read_only)
        self._extract_to.setEnabled(has_archive)
        self._compress.setEnabled(selected > 0 and not read_only and archives.available())
        self._permissions.setEnabled(selected > 0 and not read_only)
        self._paste.setEnabled(bool(clipboard.get()[0]) and not read_only)
        other = self._other_pane()
        # Copying what is in the recent files is copying the files; moving them is not
        # something a list of what was used should do
        self._copy_other.setEnabled(
            selected > 0 and not in_trash and other is not None and not other.read_only
        )
        self._move_other.setEnabled(
            selected > 0 and not read_only and other is not None and not other.read_only
        )
        for item in (self._new_folder, self._new_file):
            item.setEnabled(not read_only)
        # A terminal starts in a folder of this computer: not in the trash or a remote place
        self._terminal.setVisible(self._terminal_folder() is not None)
        self._restore.setEnabled(
            selected > 0 and all(info.original_path is not None for info in infos)
        )
        self._empty_trash.setEnabled(at_trash and self._model.rowCount() > 0)

        count = self._proxy.rowCount()
        for item in (self._select_all, self._select_matching, self._invert):
            item.setEnabled(count > 0)
        if self._error:
            text = ""
        elif self._model.loading:
            text = "Loading…"
        else:
            text = "1 item" if count == 1 else f"{count} items"
            if self._pane.search_text.strip():
                text = f"{count} found"
                if self._pane.searching:
                    text += " · searching…"
                elif self._model.search_truncated:
                    text += " · there are more: type more of the name"
            if selected == 1 and infos[0].folder:
                # A result of a search: where it is
                text += f" · {infos[0].display_name} in {infos[0].folder}"
            elif selected == 1:
                text += f" · {infos[0].display_name} selected"
            elif selected:
                text += f" · {selected} selected"
            if self._narrow and selected == 1 and infos[0].mode is not None:
                # Short, and with what the narrow list has no column to spare for
                text = f"{infos[0].display_name} · {mode_text(infos[0].mode)}"
        self._count_label.setText(text)
        self._sync_search_field()
        self._update_details(infos)
        self._preview_action.setEnabled(selected > 0)
        self._update_preview(infos)

    @property
    def preview_is_open(self):
        return self._preview.isVisible() or self._preview_sheet.is_open

    def toggle_preview(self):
        """Open the preview of the selection, or close it. In a narrow window it is a
        sheet at the bottom, otherwise a window of its own."""
        if self.preview_is_open:
            self._close_preview()
        elif self._view.selected_infos():
            self._open_preview()

    def _open_preview(self):
        infos = self._view.selected_infos()
        if self._narrow:
            # One sheet at a time
            self._show_details.setChecked(False)
            self._show_in(self._sheet_preview, infos)
            self._set_preview_sheet_open(True)
        else:
            self._show_in(self._preview, infos)
            self._preview.open_over(self)

    def _close_preview(self):
        self._preview.hide()
        self._set_preview_sheet_open(False)

    def _set_preview_sheet_open(self, open_):
        if open_ == self._preview_sheet.is_open:
            return
        self._preview_sheet.set_open(open_, animate=self.isVisible())
        self._update_sheet_actions()

    def _preview_full_screen(self):
        """A double click on the sheet: the same file in the preview window, full screen.
        Leaving the full screen closes that window, and the sheet is as it was."""
        self._show_in(self._preview, self._view.selected_infos())
        self._preview.open_over(self)
        self._preview.showFullScreen()
        self._preview.only_full_screen = True

    def _update_sheet_actions(self):
        """The keys of the preview as a sheet. Not while the preview window is up over
        it, in full screen: that window has the same keys, and they are its own then."""
        sheet = self._preview_sheet.is_open and not self._preview.isVisible()
        self._close_preview_sheet.setEnabled(sheet)
        pages = sheet and self._sheet_preview.page_count > 1
        self._next_page.setEnabled(pages)
        self._previous_page.setEnabled(pages)

    def _turn_page(self, by):
        self._sheet_preview.turn_page(by)

    def _update_preview(self, infos):
        """Make an open preview show what is selected now."""
        if not self.preview_is_open:
            return
        if not infos:
            self._close_preview()
            return
        if self._preview.isVisible():
            self._show_in(self._preview, infos)
        if self._preview_sheet.is_open:
            self._show_in(self._sheet_preview, infos)

    def _show_in(self, preview, infos):
        if len(infos) != 1:
            preview.show_message(f"{len(infos)} items selected")
        else:
            path = locations.local_path(locations.entry_location(self._location, infos[0]))
            preview.show_item(infos[0], path)

    def _on_preview_key(self, event):
        """An arrow key pressed in the preview moves the selection of the list."""
        target = self._view.focusProxy() or self._view
        QApplication.sendEvent(
            target, QKeyEvent(QEvent.Type.KeyPress, event.key(), event.modifiers())
        )

    def _update_details_soon(self, *_args):
        if not self._details_timer.isActive():
            self._details_timer.start()

    def _update_details(self, infos=None):
        if self._location is None:
            return
        if infos is None:
            infos = self._view.selected_infos()
        count = None if self._model.loading or self._error else self._proxy.rowCount()
        if self._details_wanted():
            self._details.show_items(self._location, infos, count)
        elif self._sheet.is_open:
            self._sheet_details.show_items(self._location, infos, count)

    # The narrow layout

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sheet_preview.set_sheet_height(round(self.height() * SHEET_SHARE))
        self._set_narrow(self.width() < style.px(NARROW_BELOW))
        QTimer.singleShot(0, self._align_toolbar)

    @property
    def is_narrow(self):
        return self._narrow

    def _set_narrow(self, narrow):
        if narrow == self._narrow:
            return
        if narrow:
            # No room for two lists: the active one of each tab stays
            for tab in self._tabs:
                if tab is self._tab:
                    self.set_split(False, remember=False)
                elif tab.is_split:
                    self._remove_other_pane(tab)
                    self._mark_active(tab)
        self._narrow = narrow
        if self.preview_is_open and not self._preview.isFullScreen():
            # The preview goes with the window: a sheet when narrow, a window when not
            self._close_preview()
            self._open_preview()
        if narrow:
            self._wide_sizes = self._splitter.sizes()
        else:
            self._show_details.setChecked(False)
        self._details.setVisible(self._details_wanted())
        self._sidebar.set_rail(narrow)
        self._path_bar.set_short(narrow)
        for pane in self._all_panes():
            pane.view.set_compact(narrow)
        self._split_view.setEnabled(not narrow)
        self._button_for(self._split_view).setVisible(not narrow)
        self._button_for(self._pane_toggle).setVisible(not narrow)
        self._job_status.set_compact(narrow)
        self._button_for(self._forward).setVisible(not narrow)
        self._toolbar_menu.setVisible(narrow)
        self._details_button.setVisible(narrow)
        if narrow:
            # As wide as the path it takes the place of; a wide window sets its width
            self._search_group.setMinimumWidth(0)
            self._search_group.setMaximumWidth(_NO_LIMIT)
        self._search_group.setSizePolicy(
            QSizePolicy.Policy.Expanding if narrow else QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Preferred,
        )
        if narrow:
            # All the width the rail leaves is the list's
            rail = self._sidebar.maximumWidth()
            self._splitter.setSizes([rail, max(1, self._splitter.width() - rail), 0])
        elif self._wide_sizes:
            self._splitter.setSizes(self._wide_sizes)
        self._update_state()

    def _set_sheet_open(self, open_):
        if open_ and self._narrow:
            # One sheet at a time
            self._set_preview_sheet_open(False)
        self._sheet.set_open(open_ and self._narrow, animate=self.isVisible())
        self._close_details.setEnabled(self._sheet.is_open)
        self._update_state()

    # Search

    def start_search(self):
        """Ctrl+F: to the search field, which a narrow window shows in place of the path."""
        self._show_search_field(True)
        self._search_field.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self._search_field.selectAll()

    def end_search(self):
        """Escape in the field: the whole folder again, and back to the list."""
        self._search_field.clear()
        self._to_the_list()

    def _to_the_list(self):
        self._view.setFocus()
        self._sync_search_field()

    def _on_search_text(self, text):
        self._pane.set_search(text)

    def _sync_search_field(self):
        """The field shows the search of the active pane, and in a narrow window it is
        there only while it is in use."""
        text = self._pane.search_text
        if self._search_field.text() != text:
            self._search_field.blockSignals(True)
            self._search_field.setText(text)
            self._search_field.blockSignals(False)
        self._show_search_field(bool(text) or self._search_field.hasFocus())

    def _show_search_field(self, in_use):
        shown = in_use or not self._narrow
        self._toolbar_search.setVisible(shown)
        self._toolbar_location.setVisible(not (self._narrow and in_use))

    def _show_folder_menu(self):
        button = self._button_for(self._folder_menu)
        self._on_context_menu(None, button.mapToGlobal(button.rect().bottomLeft()))

    # Items

    def _in_trash(self):
        return self._pane.in_trash

    def _in_recent(self):
        return self._pane.in_recent

    def _read_only(self):
        return self._pane.read_only

    def _at_trash(self):
        return self._pane.at_trash

    def _on_item_activated(self, info):
        target = locations.entry_location(self._location, info)
        if info.is_dir:
            self.navigate(target)
        elif self._in_trash():
            self._show_error(f"Restore “{info.display_name}” to open it.")
        else:
            self._remember_opened(target, info.content_type)
            desktop.open_default(target, self._show_error)

    def remove_from_recent(self):
        """Take the selected entries off the list of recent files. The files stay."""
        if not self._in_recent() or self._recent is None:
            return None
        selected = self._selected_locations()
        if not selected:
            return None
        pane = self._pane
        self._recent.forget(selected, pane.reload)
        return selected

    def clear_recent(self):
        """Empty the list of recent files, after asking. Whether it was done."""
        if not self._in_recent() or self._recent is None:
            return False
        count = self._model.rowCount()
        if count == 0 or not ask_to_clear_recent(self, count):
            return False
        self._recent.clear(self._pane.reload)
        return True

    def open_settings(self):
        dialog = SettingsDialog(self, self.set_show_hidden, self._clear_recent_from_settings)
        dialog.exec()

    def _clear_recent_from_settings(self):
        if self._recent is None or not ask_to_clear_recent(self):
            return
        self._recent.clear(self._pane.reload if self._in_recent() else None)

    def show_in_folder(self, info):
        """Go to the folder a file of the recent files, or a result of a search, is in,
        with the file selected."""
        target = locations.entry_location(self._location, info)
        parent = target.get_parent()
        if parent is not None:
            self.navigate(parent, select=locations.basename(target))

    # Favourites

    def _on_dropped_on_place(self, location, uris, modifiers, position):
        """Files were dropped on a place or favourite of the sidebar: into it. The trash
        takes them as it does anywhere."""
        self._drop_into(location, uris, modifiers, position, trash_ok=locations.is_trash(location))

    def _update_favourite_action(self):
        location = self._location
        usable = (
            self._favourites is not None
            and location is not None
            and not locations.in_trash(location)
            and not locations.in_recent(location)
        )
        self._favourite.setEnabled(usable)
        if usable:
            self._favourite.setText(self._favourite_text(location))

    def _favourite_text(self, location):
        found = self._favourites is not None and self._favourites.find(location)
        return "Remove from favourites" if found else "Add to favourites"

    def toggle_favourite(self, location=None, index=None):
        """Put a folder in the favourites, or take it out when it is there. By default the
        folder that is open, and a new one goes at the end or at `index`. Whether it is in
        them now, None where nothing could be done."""
        location = location or self._location
        if (
            self._favourites is None
            or location is None
            or locations.in_trash(location)
            or locations.in_recent(location)
        ):
            return None
        if not self._favourites.available:
            self._show_error("Open Dolphin once: the favourites live in its list of places.")
            return None
        name = locations.display_name(location)
        found = self._favourites.find(location)
        if found is not None:
            done = self._favourites.remove(found)
            message = f"“{name}” taken out of the favourites"
        else:
            done = self._favourites.add(location, index=index)
            message = f"“{name}” added to the favourites"
        if not done:
            self._show_error(f"Could not change the favourites: {name}.")
            return None
        self.statusBar().showMessage(message, _COPIED_MS)
        return found is None

    def _add_dropped_folders(self, uris, index=-1):
        """Folders dropped among the favourites become favourites, at `index` of the list
        (the end for -1), one after the other. Files do not, and a folder that is one
        already stays where it is."""
        for uri in uris:
            location = locations.from_uri(uri)
            if locations.is_folder(location) and self._favourites.find(location) is None:
                if self.toggle_favourite(location, None if index < 0 else index):
                    index += 1 if index >= 0 else 0

    def _show_favourite_menu(self, favourite, position):
        menu = menus.menu(self)
        menu.addAction(
            style.icon("document-open-symbolic", "document-open", menu=True),
            "Open",
            lambda: self.navigate(favourite.location),
        )
        menu.addAction(
            style.icon("tab-new-symbolic", "tab-new", menu=True),
            "Open in new tab",
            lambda: self.new_tab(favourite.location, activate=False),
        )
        if not self._narrow:
            menu.addAction(
                self._split_view.icon(),
                "Open in split view",
                lambda: self._open_location_in_split(favourite.location),
            )
        menu.addSeparator()
        menu.addAction("Rename…", lambda: self.rename_favourite(favourite))
        items = self._favourites.items
        index = items.index(favourite) if favourite in items else -1
        up = menu.addAction("Move up", lambda: self._favourites.move(favourite, -1))
        up.setEnabled(index > 0)
        down = menu.addAction("Move down", lambda: self._favourites.move(favourite, 1))
        down.setEnabled(0 <= index < len(items) - 1)
        menu.addSeparator()
        menu.addAction(
            style.icon("edit-delete-symbolic", "edit-delete", colour="danger", menu=True),
            "Remove from favourites",
            lambda: self._favourites.remove(favourite),
        )
        menu.exec(position)

    def rename_favourite(self, favourite):
        def apply(text):
            text = text.strip()
            if not text:
                return "A favourite needs a name."
            if not self._favourites.rename(favourite, text):
                return "Could not change the favourites."
            return None

        return self._ask_name(
            "Rename favourite",
            f"New name for “{favourite.title}”:",
            favourite.title,
            apply,
        )

    def _open_with(self, app, target, content_type):
        self._remember_opened(target, content_type)
        desktop.open_with(app, target, self._show_error)

    def _remember_opened(self, target, content_type):
        """Tell the desktop's lists of recent files, unless the user would rather not."""
        if self._recent is not None:
            self._recent.add(target, content_type)

    def _set_remember_opened(self, forget):
        settings.set_remember_opened(not forget)
        self.statusBar().showMessage(
            "Opened files are not remembered" if forget else "Opened files are remembered again",
            _COPIED_MS,
        )

    def _set_density(self, density):
        """Sizes are set when the widgets are made, so it applies at the next start."""
        settings.set_density(density)
        self.statusBar().showMessage(
            f"{density.capitalize()} density applies at the next start", _COPIED_MS
        )

    def _on_remember_opened_changed(self, remember):
        """Changed here or in another window: it is one setting."""
        self._private.setChecked(not remember)
        self._private_button.setVisible(not remember)

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
                menu.addAction(self._empty_trash)
                menu.addActions(self._selecting())
                menu.addSeparator()
                self._add_sort_and_view(menu)
            return menu
        if self._in_recent():
            return self._recent_menu(menu, info)
        if info is None:
            # The folder's own menu
            menu.addActions([self._new_folder, self._new_file])
            menu.addSeparator()
            menu.addAction(self._paste)
            menu.addActions(self._selecting())
            menu.addSeparator()
            self._add_sort_and_view(menu)
            menu.addAction(self._hidden)
            menu.addAction(self._private)
            menu.addSeparator()
            menu.addAction(self._favourite)
            menu.addActions([self._copy_path, self._terminal])
            self._add_open_in_manager(menu, self._location)
            menu.addAction(self._properties)
            return menu
        target = locations.child(self._location, info.name)
        folder = self._location
        menu.addAction(
            style.icon("document-open-symbolic", "document-open", menu=True),
            "Open",
            lambda: self._on_item_activated(info),
        )
        if info.folder:
            # A result of a search, from a folder below this one
            menu.addAction(
                style.icon("folder-open-symbolic", "folder-open", menu=True),
                "Show in folder",
                lambda: self.show_in_folder(info),
            )
        if info.is_dir:
            folder = target
            menu.addAction(
                style.icon("tab-new-symbolic", "tab-new", menu=True),
                "Open in new tab",
                lambda: self._open_in_new_tab(info),
            )
            if not self._narrow:
                menu.addAction(
                    self._split_view.icon(),
                    "Open in split view",
                    lambda: self._open_in_split(info),
                )
        else:
            self._add_open_with(menu, info, target)
        self._add_open_in_manager(menu, folder)
        menu.addSeparator()
        if not info.is_dir:
            menu.addAction(self._preview_action)
        menu.addActions([self._cut, self._copy, self._paste])
        menu.addSeparator()
        menu.addActions([self._rename, self._duplicate, self._link, self._move_to])
        if self._is_archive(info) and not self._in_trash():
            menu.addActions([self._extract_here, self._extract_to])
        if archives.available() and not self._read_only():
            menu.addAction(self._compress)
        if self.is_split:
            menu.addActions([self._copy_other, self._move_other])
        menu.addActions([self._trash, self._delete])
        menu.addSeparator()
        menu.addAction(self._select_type_action(menu, info))
        menu.addAction(self._copy_path)
        if info.is_dir and self._favourites is not None:
            menu.addAction(
                self._favourite.icon(),
                self._favourite_text(target),
                lambda: self.toggle_favourite(target),
            )
        if info.is_dir and self._terminal_folder(folder) is not None:
            # In the folder the menu is for, not the one it is listed in
            menu.addAction(
                self._terminal.icon(), "Open terminal here", lambda: self.open_terminal(folder)
            )
        menu.addSeparator()
        menu.addMenu(self._permissions_menu(menu))
        menu.addAction(self._properties)
        return menu

    def _recent_menu(self, menu, info):
        """What a file of the recent files can do: be opened, found, copied or looked at,
        and be taken off the list."""
        if info is None:
            menu.addActions(self._selecting())
            menu.addSeparator()
            self._add_sort_and_view(menu)
            menu.addAction(self._private)
            menu.addSeparator()
            menu.addAction(self._clear_recent)
            return menu
        target = locations.entry_location(self._location, info)
        menu.addAction(
            style.icon("document-open-symbolic", "document-open", menu=True),
            "Open",
            lambda: self._on_item_activated(info),
        )
        if not info.is_dir:
            self._add_open_with(menu, info, target)
        menu.addAction(
            style.icon("folder-open-symbolic", "folder-open", menu=True),
            "Show in folder",
            lambda: self.show_in_folder(info),
        )
        menu.addSeparator()
        if not info.is_dir:
            menu.addAction(self._preview_action)
        menu.addActions([self._copy, self._copy_path])
        menu.addAction(self._select_type_action(menu, info))
        menu.addSeparator()
        menu.addAction(self._forget)
        menu.addAction(self._properties)
        return menu

    def _selecting(self):
        """The folder menu's entries for what is selected."""
        return [self._select_all, self._select_matching, self._invert]

    def _select_type_action(self, menu, info):
        item = QAction(self._select_all.icon(), "Select all of this type", menu)
        item.triggered.connect(lambda: self.select_same_type(info))
        return item

    def _add_open_with(self, menu, info, target):
        apps = desktop.apps_for_type(info.content_type)
        if not apps:
            return
        open_with = menus.menu(menu, "Open with")
        open_with.setIcon(style.icon("view-grid-symbolic", "view-grid", menu=True))
        menu.addMenu(open_with)
        for app in apps:
            open_with.addAction(
                QIcon.fromTheme(app.icon) if app.icon else QIcon(),
                app.name,
                lambda app=app: self._open_with(app, target, info.content_type),
            )

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
        if not self._view.selected_infos() or self._read_only():
            return
        self._permissions_menu(self).exec(QCursor.pos())

    def _add_open_in_manager(self, menu, folder):
        manager = desktop.file_manager()
        if manager is not None:
            menu.addAction(
                style.icon("folder-open-symbolic", "folder-open", menu=True),
                f"Open folder in {manager.name}",
                lambda: desktop.open_with(manager, folder, self._show_error),
            )

    def _add_sort_and_view(self, menu):
        """The folder menu's entries for how the list is shown."""
        column, order = self._view.sort_column(), self._view.sort_order()
        sort = menus.menu(menu, "Sort by")
        sort.setIcon(style.icon("view-sort-symbolic", "view-sort", menu=True))
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
        view.setIcon(style.icon("view-grid-symbolic", "view-grid", menu=True))
        view.addActions([self._details_mode, self._icons_mode])
        if settings.per_folder():
            # The view and the sort order that were chosen in this folder, given up
            default = view.addAction("Use the default here", self._pane.use_default_view)
            default.setEnabled(self._pane.has_own_view)
        view.addSeparator()
        view.addAction(self._split_view)
        view.addAction(self._pane_toggle)
        view.addAction(self._new_tab)
        density = menus.menu(view, "Density")
        current = settings.density()
        for key, text in (("comfortable", "Comfortable"), ("compact", "Compact")):
            item = density.addAction(text, lambda key=key: self._set_density(key))
            item.setCheckable(True)
            item.setChecked(key == current)
        view.addMenu(density)
        menu.addMenu(view)

    # File operations

    def _selected_locations(self):
        return self._pane.selected_locations()

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
        if not uris or self._read_only():
            return None
        sources = [locations.from_uri(uri) for uri in uris]
        if cut:
            # A cut is used up by its paste
            clipboard.clear()
            if self._all_in(sources, self._location):
                return None
        return self._start(JobKind.MOVE if cut else JobKind.COPY, sources, self._location)

    def duplicate(self):
        """A copy of each selected item beside itself, named as "Keep both" names it."""
        selected = self._selected_locations()
        if not selected or self._read_only():
            return None
        return self._start(JobKind.DUPLICATE, selected)

    def create_link(self):
        """A symbolic link to each selected item, beside it."""
        selected = self._selected_locations()
        if not selected or self._read_only():
            return []
        # Each beside what it points to, which for a result of a search is further down
        made = []
        errors = []
        for target in selected:
            links, failed = quick.create_links([target], locations.parent(target))
            made += links
            errors += failed
        return self._after_linking(made, errors, len(selected), self._location)

    def _link_into(self, sources, folder):
        """A link in `folder` to each of the files dropped there."""
        made, errors = quick.create_links(sources, folder)
        return self._after_linking(made, errors, len(sources), folder)

    def _after_linking(self, made, errors, count, folder):
        if len(made) == 1:
            self._select_when_listed(folder, locations.basename(made[0]))
        if not errors:
            self.statusBar().showMessage(
                "Link created" if count == 1 else f"{count} links created", _COPIED_MS
            )
        elif len(errors) == 1:
            self._show_error(f"Could not create a link to {errors[0]}.")
        else:
            # Every one of them, by name
            summary = f"Could not create {len(errors)} of {count} links."
            self._show_error(summary)
            ErrorDetails(SimpleNamespace(errors=errors, summary=summary), self).open()
        return made

    # Selecting

    def select_matching(self):
        """Ask for a pattern or a few words, and select what has them in its name."""
        if not self._proxy.rowCount():
            return None
        view = self._view

        def picks(text):
            wanted = search.selector(text)
            return lambda info: wanted(info.display_name)

        def apply(text, add):
            view.select_where(picks(text), add)
            view.setFocus()

        def count(text):
            wanted = picks(text)
            return sum(wanted(info) for info in self._pane.shown_infos())

        dialog = SelectDialog(self, count, apply)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.open()
        return dialog

    def invert_selection(self):
        self._view.invert_selection()

    def select_same_type(self, info):
        """Select everything of the type of `info`: every folder for a folder."""
        return self._view.select_where(
            lambda other: (
                other.is_dir == info.is_dir
                and (info.is_dir or other.content_type == info.content_type)
            )
        )

    def trash_selected(self):
        if self._in_recent():
            # The Delete key takes the entry off the list
            return self.remove_from_recent()
        if self._in_trash():
            # Already there: the Delete key means delete for good, which asks first
            return self.delete_selected()
        selected = self._selected_locations()
        return self._start(JobKind.TRASH, selected) if selected else None

    def delete_selected(self):
        """Delete for good. The job asks for confirmation before it touches anything."""
        if self._in_recent():
            return None
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
        if not selected or self._read_only():
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
        if not selected or self._read_only():
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

    # Archives

    @staticmethod
    def _is_archive(info):
        return not info.is_dir and archives.can_extract(info.content_type)

    def _selected_archives(self):
        return [
            locations.entry_location(self._location, info)
            for info in self._view.selected_infos()
            if self._is_archive(info)
        ]

    def extract_here(self):
        """Extract the selected archives beside themselves, a job each."""
        if self._read_only() or self._in_trash():
            return []
        return self._extract(self._location)

    def extract_to(self):
        """Extract the selected archives into a folder that is picked in a dialog."""
        selected = self._selected_archives()
        if not selected or self._in_trash():
            return []
        start = locations.local_path(self._location) or locations.local_path(locations.home())
        path = pick_folder(self, "Extract to", start)
        return self._extract(locations.from_path(path)) if path else []

    def _extract(self, destination):
        return [
            self._jobs.extract(archive, destination, origin=self)
            for archive in self._selected_archives()
        ]

    def compress(self):
        """Compress the selection into an archive in this folder, after asking for its
        name and kind."""
        selected = self._selected_locations()
        if not selected or self._read_only() or not archives.available():
            return None
        folder = self._location
        names = [locations.basename(location) for location in selected]
        what = f"“{names[0]}”" if len(names) == 1 else f"{len(names)} items"

        def apply(text, format):
            name = text.strip()
            if not name or "/" in name or name in (".", ".."):
                return "The archive needs a name, without “/”."
            extension = archives.format_for(format).extension
            if not name.endswith(extension):
                name += extension
            self._jobs.compress(selected, folder, name, format, origin=self)
            return None

        dialog = CompressDialog(
            self,
            what,
            archives.FORMATS,
            lambda format: archives.archive_name(names, locations.basename(folder), format),
            apply,
        )
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.open()
        return dialog

    def copy_to_other_pane(self):
        return self._transfer(JobKind.COPY)

    def move_to_other_pane(self):
        return self._transfer(JobKind.MOVE)

    def _transfer(self, kind):
        """Copy or move the selection into the folder of the other pane."""
        other = self._other_pane()
        selected = self._selected_locations()
        if other is None or not selected or self._in_trash() or other.read_only:
            return None
        if kind is JobKind.MOVE and self._read_only():
            return None
        if kind is JobKind.MOVE and self._all_in(selected, other.location):
            # Already there
            return None
        return self._start(kind, selected, other.location)

    def new_file(self):
        if self._read_only():
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
        if self._read_only():
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
        if not infos or self._read_only():
            return None
        if len(infos) > 1:
            return self._rename_many(infos)
        info = infos[0]
        folder = self._location
        target = locations.child(folder, info.name)

        def apply(text):
            try:
                renamed = quick.rename(target, text)
            except OperationError as error:
                return f"Could not rename “{info.display_name}”: {error.message}."
            self._select_when_listed(folder, locations.basename(renamed))
            # Nothing watches the folders below: a result that was renamed is found again
            self._pane.search_again()
            return None

        # Not `info.name`: for a result of a search that is its path below this folder
        name = locations.basename(target)
        stem = name if info.is_dir else split_extension(name)[0]
        return self._ask_name(
            "Rename", f"New name for “{info.display_name}”:", name, apply, len(stem)
        )

    def _rename_many(self, infos):
        """Several at once, in a dialog that shows every new name first."""
        pane = self._pane
        folder = self._location

        def renamed(now):
            names = {locations.name_below(folder, location) for location in now}
            pane.select_names_when_listed(folder, names)
            # Nothing watches the folders below: results that were renamed are found again
            pane.search_again()
            count = len(now)
            self.statusBar().showMessage(f"{count} items renamed", _COPIED_MS)

        dialog = RenameManyDialog(
            self,
            [(locations.child(folder, info.name), info.is_dir) for info in infos],
            renamed,
        )
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.open()
        return dialog

    def _ask_name(self, title, label, name, apply, select_to=None):
        dialog = NameDialog(self, title, label, name, apply, select_to)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.open()
        return dialog

    def _select_when_listed(self, folder, name):
        self._pane.select_when_listed(folder, name)

    @staticmethod
    def _all_in(sources, folder):
        return all(locations.same(locations.parent(source), folder) for source in sources)

    def _on_dropped(self, uris, folder_info, modifiers, position):
        """Files were dropped on the list, or on a folder in it."""
        return self._drop_on(self._pane, uris, folder_info, modifiers, position)

    def _drop_on(self, pane, uris, folder_info, modifiers, position):
        if pane.read_only and not pane.at_trash:
            # Nothing is put into the recent files, and not into a trashed folder
            return None
        target = pane.location
        if folder_info is not None:
            target = locations.child(target, folder_info.name)
        # The trash itself takes what is dropped on it, and nothing inside it does
        trash_ok = pane.at_trash and folder_info is None
        return self._drop_into(target, uris, modifiers, position, trash_ok)

    def _drop_into(self, target, uris, modifiers, position, trash_ok=False):
        """Move or copy dropped files into a folder, by the keys held and the file systems."""
        if locations.in_recent(target):
            return None
        sources = [locations.from_uri(uri) for uri in uris]
        if any(locations.same(source, target) for source in sources):
            # A folder dropped on itself
            return None
        if locations.in_trash(target):
            # Dropped on the trash: that is where they go. Not into a trashed folder
            if not trash_ok:
                return None
            sources = [source for source in sources if not locations.in_trash(source)]
            return self._start(JobKind.TRASH, sources) if sources else None

        keys = Qt.KeyboardModifier
        both = keys.ControlModifier | keys.ShiftModifier
        if modifiers & keys.AltModifier:
            kind = self._ask_drop_action(position)
        elif modifiers & both == both:
            # As in Dolphin: a link to what was dragged
            kind = LINK
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
        if kind == LINK:
            return self._link_into(sources, target)
        if kind is JobKind.MOVE and self._all_in(sources, target):
            # Already where they were dropped
            return None
        return self._start(kind, sources, target)

    def _ask_drop_action(self, position):
        menu = menus.menu(self)
        move = menu.addAction(
            style.icon("go-jump-symbolic", "transform-move", menu=True), "Move here"
        )
        copy = menu.addAction(style.icon("edit-copy-symbolic", "edit-copy", menu=True), "Copy here")
        link = menu.addAction(style.icon("edit-link-symbolic", "edit-link", menu=True), "Link here")
        menu.addSeparator()
        menu.addAction("Cancel")
        return {move: JobKind.MOVE, copy: JobKind.COPY, link: LINK}.get(menu.exec(position))

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
        self._preview.hide()
        if not self._other_windows_open():
            # The last window: where it was is where the next start begins
            settings.set_session(*self.session())
        for pane in self._all_panes():
            pane.close()
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
            for pane in self._all_panes():
                pane.model.recount()

    def _on_jobs_idle(self):
        for pane in self._all_panes():
            pane.model.recount()
            # What a job moved or deleted below a folder is not told by any monitor
            pane.search_again()
        # What a job copied or deleted shows in how full the drives are
        self._volumes.refresh_usage()
        if self._closing_when_idle:
            self._closing_when_idle = False
            self.close()
            if not self._other_windows_open():
                # Closing a hidden window doesn't end the app by itself
                QTimer.singleShot(0, quit_app)
