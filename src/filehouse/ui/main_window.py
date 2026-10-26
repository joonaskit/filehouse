"""One browser window: toolbar, sidebar, file list, operations panel and status bar."""

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
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

from filehouse.core import locations, search
from filehouse.core.listing import mode_text
from filehouse.core.operations import JobKind
from filehouse.integration import desktop, style
from filehouse.qt_models.job_model import JobModel
from filehouse.qt_models.volumes import Volumes
from filehouse.qt_models.workers import Background
from filehouse.ui import clipboard, groups, settings
from filehouse.ui.actions import RULES_ICON, Actions
from filehouse.ui.context_menus import ContextMenus
from filehouse.ui.details_pane import DetailsPane
from filehouse.ui.dialogs.name import ask_name
from filehouse.ui.dialogs.prompter import Prompter
from filehouse.ui.dialogs.properties import PropertiesDialog
from filehouse.ui.dialogs.rules import FolderRulesDialog, RunRulesDialog
from filehouse.ui.dialogs.select import SelectDialog
from filehouse.ui.dialogs.settings import SettingsDialog
from filehouse.ui.file_actions import FileActions
from filehouse.ui.folder_view import history_step
from filehouse.ui.job_status import JobStatus
from filehouse.ui.operations_panel import OperationsPanel
from filehouse.ui.pane import Pane
from filehouse.ui.path_bar import PathBar
from filehouse.ui.preview import SHEET_SHARE, PreviewWindow
from filehouse.ui.search_field import SearchField
from filehouse.ui.sheet import Sheet
from filehouse.ui.sidebar import Sidebar
from filehouse.ui.slide import SideSlide
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


def quit_app():
    """End the process, as closing the last window does: not when it is to stay."""
    if QApplication.quitOnLastWindowClosed():
        QApplication.quit()


def session_to_open():
    """The tabs of last time, to be opened again: a list of (the one or two folders of a
    tab, which of them is the active one) and the tab in front, or None.

    Nothing is looked at here: on a share that does not answer that could hold up the
    start for long. A folder that turns out to be gone is dealt with once the window
    has it, by `MainWindow`: the nearest one above it that is there, or none."""
    session = settings.session() if settings.restore_tabs() else None
    if session is None:
        return None
    tabs, front = session
    opened = [
        ([locations.from_uri(uri) for uri in uris], active if 0 <= active < len(uris) else 0)
        for uris, active in tabs
    ]
    return opened, front if 0 <= front < len(opened) else 0


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
        self.setWindowIcon(style.app_icon())
        self.setMinimumSize(style.px(_MINIMUM_SIZE[0]), style.px(_MINIMUM_SIZE[1]))
        self.resize(settings.window_size(QSize(style.px(1080), style.px(660))))

        self._closing_when_idle = False
        self._narrow = False
        self._wide_sizes = None

        if jobs is None:
            jobs = JobModel(self)
            self._prompter = Prompter(jobs, self)
        self._jobs = jobs
        self._file_actions = FileActions(
            self,
            jobs,
            pane=lambda: self._pane,
            other_pane=self._other_pane,
            say=lambda text: self.statusBar().showMessage(text, _COPIED_MS),
            show_error=self.show_error,
        )
        self._recent = recent
        self._favourites = favourites
        self._jobs.idle.connect(self._on_jobs_idle)
        # What is only looked at (a typed path, what was dropped, the special folders)
        # is looked at here, and the window goes on meanwhile
        self._background = Background(self, in_turn=False)
        self._entered = 0  # paths typed, the last of which is gone to

        # Tabs, each with a folder or two side by side. `_tab` is the shown one and
        # `_pane` its active pane: the toolbar, the title, the details, the status bar
        # and every action are about it
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
        self._volumes.failed.connect(self.show_error)
        # Every special folder that is set, and the ones that are not there taken out
        # once that is known
        self._sidebar = Sidebar(locations.places_as_set(), self._volumes, self._favourites)
        self._background.call(locations.places, self._sidebar.set_places)
        self._sidebar.place_activated.connect(self.navigate)
        self._sidebar.new_tab_requested.connect(lambda place: self.new_tab(place, activate=False))
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
        self._menus = ContextMenus(
            self, self._actions, lambda: self._pane, self._sidebar, self._favourites
        )
        self._sidebar.favourite_menu_requested.connect(self._menus.show_favourite)
        self._sidebar.place_menu_requested.connect(self._menus.show_place)
        self._sheet_details.close_requested.connect(
            lambda: self._actions.show_details.setChecked(False)
        )

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
        self._details_slide = SideSlide(splitter, self._details)
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
        self._details_button.setDefaultAction(self._actions.show_details)
        self._actions.on_buttons.append(self._actions.show_details)
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
        self._actions.on_buttons.append(self._actions.private)
        self._draw_action_icons()
        self._private_button.setText("Private")
        self._private_button.clicked.connect(self._actions.private.trigger)
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
        self._actions = actions = Actions(
            self,
            {
                "back": self.go_back,
                "forward": self.go_forward,
                "up": self.go_up,
                "edit_location": self._path_bar.edit,
                "reload": self.reload,
                "close": self.close_current,
                "new_tab": self.new_tab,
                "next_tab": lambda: self._step_tab(1),
                "previous_tab": lambda: self._step_tab(-1),
                "hidden": self._set_show_hidden,
                "private": self._set_remember_opened,
                "icons_mode": lambda: self.set_view_mode(ICONS),
                "details_mode": lambda: self.set_view_mode(DETAILS),
                "split_view": self.set_split,
                "pane_toggle": self.set_details_pane,
                "copy": self.copy,
                "cut": self.cut,
                "paste": self.paste,
                "rename": self.rename,
                "undo": self.undo,
                "duplicate": self.duplicate,
                "link": self.create_link,
                "trash": self.trash_selected,
                "delete": self.delete_selected,
                "new_folder": self.new_folder,
                "new_file": self.new_file,
                "move_to": self.move_selected,
                "extract_here": self.extract_here,
                "extract_to": self.extract_to,
                "compress": self.compress,
                "copy_other": self.copy_to_other_pane,
                "move_other": self.move_to_other_pane,
                "copy_path": self.copy_path,
                "terminal": lambda: self.open_terminal(),
                "select_all": lambda: self._view.select_all(),
                "select_matching": self.select_matching,
                "invert": self.invert_selection,
                "restore": self.restore_selected,
                "empty_trash": self.empty_trash,
                "properties": self.properties,
                "close_details": lambda: self._actions.show_details.setChecked(False),
                "preview": self.toggle_preview,
                "close_preview_sheet": self._close_preview,
                "next_page": lambda: self._turn_page(1),
                "previous_page": lambda: self._turn_page(-1),
                "forget": self.remove_from_recent,
                "clear_recent": self.clear_recent,
                "favourite": self.toggle_favourite,
                "permissions": lambda: self._menus.show_permissions(),
                "folder_rules": self.open_folder_rules,
                "run_rules": self.run_folder_rules,
                "settings": self.open_settings,
                "folder_menu": self._show_folder_menu,
                "search": self.start_search,
            },
        )
        actions.hidden.setChecked(self._proxy.show_hidden)
        actions.private.setChecked(not settings.remember_opened())
        actions.pane_toggle.setChecked(settings.details_pane())
        settings.changes.details_pane_changed.connect(self._on_details_pane_changed)
        # Whether "Run rules now" is offered here
        settings.changes.rules_changed.connect(self._update_state)
        self._jobs.undo_changed.connect(self._update_undo)
        self._update_undo()
        # Not `triggered`: the sheet's own button and Escape uncheck it too
        actions.show_details.toggled.connect(self._set_sheet_open)

        # One row of rounded groups, over the panes they go with: back and forward over
        # the sidebar, the path and how the list is shown over the list, and search over
        # the details. What is done to the selection is in its menu, and in the bar at
        # the bottom of a narrow window
        toolbar = QToolBar()
        toolbar.setMovable(False)
        toolbar.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        # The first group is as wide as the sidebar under it, and the search field with
        # the settings button as wide as the details pane: see _align_toolbar
        self._navigation_group = self._group(actions.back, actions.forward, actions.up)
        self._navigation_group.layout().addStretch(1)
        toolbar.addWidget(self._navigation_group)
        location = self._group()
        location.layout().addWidget(self._path_bar)
        # The folder open here has rules (#96): dimmed while none of them would run
        self._rules_button = groups.icon_button()
        self._rules_button.setAccessibleName("Rules for this folder")
        self._rules_button.clicked.connect(self.open_folder_rules)
        self._rules_button.setVisible(False)
        location.layout().addWidget(self._rules_button)
        location.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._toolbar_location = toolbar.addWidget(location)
        self._view_group = self._group(
            actions.details_mode,
            actions.icons_mode,
            actions.split_view,
            actions.pane_toggle,
        )
        toolbar.addWidget(self._view_group)
        # In a wide window the field is always there; in a narrow one it takes the
        # place of the path while it is in use, and a button opens it
        self._search_group = self._group()
        self._search_group.layout().addWidget(self._search_field)
        self._search_group.setFixedWidth(style.px(_SEARCH_WIDTH))
        self._toolbar_search = toolbar.addWidget(self._search_group)
        self._toolbar_menu = toolbar.addWidget(self._group(actions.search, actions.folder_menu))
        self._toolbar_menu.setVisible(False)
        self._settings_group = self._group(actions.settings)
        toolbar.addWidget(self._settings_group)
        self._toolbar = toolbar
        self.addToolBar(toolbar)
        for pane in (self._sidebar, self._details):
            pane.installEventFilter(self)
        if self._favourites is not None:
            self._favourites.changed.connect(self._update_favourite_action)

    def _draw_action_icons(self):
        """The icon of every action, in the colours of the theme in use."""
        self._actions.draw_icons()
        self._private_button.setIcon(self._actions.private.icon())
        # Not the action's own icon: this one is dimmed by a colour, and stays clickable
        self._rules_icons = {
            running: style.icon(
                RULES_ICON + "-symbolic",
                RULES_ICON,
                colour="textPrimary" if running else "textTertiary",
            )
            for running in (True, False)
        }

    def _retheme(self):
        """The theme changed while the window is open: icons are drawn in its colours."""
        self._draw_action_icons()
        self._update_rules_button()
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
        self._actions.on_buttons.extend(actions)
        return groups.group(*actions)

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
        view.item_activated.connect(for_this_pane(self.open_entry))
        view.context_menu_requested.connect(for_this_pane(self._on_context_menu))
        view.dropped.connect(for_this_pane(self._on_dropped))
        view.open_in_tab.connect(for_this_pane(self.open_in_new_tab))
        view.history_requested.connect(for_this_pane(self._go_in_history))
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
            self._actions.split_view.setChecked(self.is_split)
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
        self._actions.split_view.setChecked(on)
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
        return not self._narrow and not self.is_split and self._actions.pane_toggle.isChecked()

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
        self._actions.pane_toggle.setChecked(show)
        self._sync_details(animate=True)
        self._update_state()

    def _sync_details(self, animate=False):
        """No room for the details beside two lists: they come and go with the split,
        at once. Asked for or put away by the user, the pane slides."""
        self._details_slide.set_shown(self._details_wanted(), animate)

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

    def open_in_new_tab(self, info):
        """A folder of the list, in a tab behind this one."""
        self.new_tab(locations.entry_location(self._location, info), activate=False)

    def open_in_split(self, info):
        """A folder of the list, in the other pane."""
        self.open_location_in_split(locations.entry_location(self._location, info))

    def open_location_in_split(self, target):
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
        self._actions.split_view.setChecked(self.is_split)
        self._sync_details()
        self._on_location_changed()
        self._view.setFocus()

    def _on_tab_moved(self, source, target):
        self._tabs.insert(target, self._tabs.pop(source))

    def _on_tab_dropped(self, index, uris, modifiers, position):
        """Files were dropped on a tab: into the folder it shows."""
        self._file_actions.drop_on(self._tabs[index].active, uris, None, modifiers, position)

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
        (
            self._actions.icons_mode if self._view.mode == ICONS else self._actions.details_mode
        ).setChecked(True)

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
        """Open the tabs of last time: the first is the one this window was made with.
        A folder that turns out to be gone is the nearest one above it that is there;
        with none, its pane or its tab goes (`_drop_gone`)."""
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
        for tab in self._tabs:
            for pane in tab.panes:
                pane.fall_back_if_gone(lambda pane=pane: self._drop_gone(pane))
        self._tab_bar.setCurrentIndex(front)

    def _drop_gone(self, pane):
        """Nothing is left of a folder of last time: the other pane of its split takes
        the tab, or another tab takes its place. In the last tab, home."""
        tab = next((tab for tab in self._tabs if pane in tab.panes), None)
        if tab is None:
            return
        if len(tab.panes) > 1:
            if tab.active is pane:
                tab.active = next(other for other in tab.panes if other is not pane)
            if tab is self._tab:
                self.set_split(False, remember=False)
            else:
                self._remove_other_pane(tab)
                self._update_tab_titles()
        elif len(self._tabs) > 1:
            self.close_tab(self._tabs.index(tab))
        else:
            pane.open_instead(locations.home())

    def set_show_hidden(self, show):
        self._actions.hidden.setChecked(show)
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

    def _go_in_history(self, step):
        if step < 0:
            self.go_back()
        else:
            self.go_forward()

    # The back and forward buttons of a mouse. Over a list they are that list's (see
    # FolderView.history_requested); anywhere else in the window a press that nothing
    # took comes here, and is the active list's

    def mousePressEvent(self, event):
        step = history_step(event)
        if step:
            self._go_in_history(step)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        step = history_step(event)
        if step:
            # The second of two quick presses
            self._go_in_history(step)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def reload(self):
        self._pane.reload()

    def _on_location_changed(self):
        location = self._pane.location
        self.setWindowTitle(f"{locations.display_name(location)} — Files")
        self._path_bar.set_location(locations.segments(location), locations.display_path(location))
        self._sidebar.select(location)
        self._actions.back.setEnabled(self._pane.history.can_go_back)
        self._actions.forward.setEnabled(self._pane.history.can_go_forward)
        self._actions.up.setEnabled(locations.parent(location) is not None)
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
            self.show_error("The drive this window was showing is gone.")

    def _on_path_entered(self, text):
        """Go where was typed, once it is known what that is. Only the last path typed."""
        self._entered += 1
        entered = self._entered

        def go(target):
            if entered != self._entered:
                return
            if target.message:
                self.statusBar().showMessage(target.message, _MESSAGE_MS)
                return
            if locations.same(target.location, self._location):
                if target.select:
                    self._view.select_name(target.select)
            else:
                self.navigate(target.location, select=target.select)
            self._view.setFocus()

        self._background.call(lambda: locations.resolve_start(text), go)

    # Model state

    def _update_state(self, *_args):
        self._pane.sync()
        self._sync_view_mode()
        infos = self._view.selected_infos()
        selected = len(infos)
        self._actions.update(
            self._pane, infos, self._other_pane(), self.terminal_folder() is not None
        )
        self._update_favourite_action()
        self._update_rules_button()
        count = self._proxy.rowCount()
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
        self._update_preview(infos)

    def _update_rules_button(self):
        """Shown while the folder open here has rules, and dimmed, saying why, while
        none of them would run. It follows the stored rules and the switch in Settings:
        both send `rules_changed`, which updates the state."""
        rules = settings.folder_rules(locations.uri(self._location))
        switched_on = any(rule.enabled for rule in rules)
        running = switched_on and settings.run_rules()
        if not rules:
            tip = ""
        elif not switched_on:
            tip = "The rules of this folder are all switched off. Click to see them."
        elif not settings.run_rules():
            tip = "Folder rules are paused in Settings. Click to see this folder's rules."
        else:
            tip = "This folder has rules that run by themselves. Click to see them."
        self._rules_button.setVisible(bool(rules))
        self._rules_button.setIcon(self._rules_icons[running])
        self._rules_button.setToolTip(tip)

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
            self._actions.show_details.setChecked(False)
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
        if not open_:
            self._sheet_preview.stop()
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
        self._actions.close_preview_sheet.setEnabled(sheet)
        pages = sheet and self._sheet_preview.page_count > 1
        self._actions.next_page.setEnabled(pages)
        self._actions.previous_page.setEnabled(pages)

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
        if not infos:
            preview.show_message("Nothing selected")
        else:
            preview.show_items(
                infos,
                lambda info: locations.local_path(locations.entry_location(self._location, info)),
            )

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
            self._actions.show_details.setChecked(False)
        self._details_slide.set_shown(self._details_wanted(), animate=False)
        self._sidebar.set_rail(narrow)
        self._path_bar.set_short(narrow)
        for pane in self._all_panes():
            pane.view.set_compact(narrow)
        self._actions.split_view.setEnabled(not narrow)
        self._button_for(self._actions.split_view).setVisible(not narrow)
        self._button_for(self._actions.pane_toggle).setVisible(not narrow)
        self._job_status.set_compact(narrow)
        self._button_for(self._actions.forward).setVisible(not narrow)
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
        self._actions.close_details.setEnabled(self._sheet.is_open)
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
        button = self._button_for(self._actions.folder_menu)
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

    def open_entry(self, info):
        target = locations.entry_location(self._location, info)
        if info.is_dir:
            self.navigate(target)
        elif self._in_trash():
            self.show_error(f"Restore “{info.display_name}” to open it.")
        else:
            self._remember_opened(target, info.content_type)
            desktop.open_default(target, self.show_error)

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

    def open_folder_rules(self):
        """The rules of the folder open here, to change, save and run (#87)."""
        dialog = FolderRulesDialog(self, self._location, self._jobs, settings.stay_running())
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.open()
        return dialog

    def run_folder_rules(self):
        """What the rules of the folder open here would do now, and Run."""
        dialog = RunRulesDialog(self, self._location, self._jobs)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.open()
        return dialog

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
        self._file_actions.drop_into(
            location, uris, modifiers, position, trash_ok=locations.is_trash(location)
        )

    def _update_favourite_action(self):
        location = self._location
        usable = (
            self._favourites is not None
            and location is not None
            and not locations.in_trash(location)
            and not locations.in_recent(location)
        )
        self._actions.favourite.setEnabled(usable)
        if usable:
            self._actions.favourite.setText(self.favourite_text(location))

    def favourite_text(self, location):
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
            self.show_error("Open Dolphin once: the favourites live in its list of places.")
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
            self.show_error(f"Could not change the favourites: {name}.")
            return None
        self.statusBar().showMessage(message, _COPIED_MS)
        return found is None

    def _add_dropped_folders(self, uris, index=-1):
        """Folders dropped among the favourites become favourites, at `index` of the list
        (the end for -1), one after the other. Files do not, and a folder that is one
        already stays where it is."""
        dropped = [locations.from_uri(uri) for uri in uris]

        def add(folders):
            nonlocal index
            for location in folders:
                if self._favourites.find(location) is None:
                    if self.toggle_favourite(location, None if index < 0 else index):
                        index += 1 if index >= 0 else 0

        # Which of them are folders is found out on a worker
        self._background.call(
            lambda: [location for location in dropped if locations.is_folder(location)], add
        )

    def rename_favourite(self, favourite):
        def apply(text):
            text = text.strip()
            if not text:
                return "A favourite needs a name."
            if not self._favourites.rename(favourite, text):
                return "Could not change the favourites."
            return None

        return ask_name(
            self,
            "Rename favourite",
            f"New name for “{favourite.title}”:",
            favourite.title,
            apply,
        )

    def open_with(self, app, target, content_type):
        self._remember_opened(target, content_type)
        desktop.open_with(app, target, self.show_error)

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

    def set_density(self, density):
        """Sizes are set when the widgets are made, so it applies at the next start."""
        settings.set_density(density)
        self.statusBar().showMessage(
            f"{density.capitalize()} density applies at the next start", _COPIED_MS
        )

    def _on_remember_opened_changed(self, remember):
        """Changed here or in another window: it is one setting."""
        self._actions.private.setChecked(not remember)
        self._private_button.setVisible(not remember)

    def _open_selected(self):
        infos = self._view.selected_infos()
        if len(infos) == 1:
            self.open_entry(infos[0])

    def show_error(self, message):
        self.statusBar().showMessage(message, _MESSAGE_MS)

    def _on_context_menu(self, info, position, above=False):
        """The menu of an entry, or of the folder for None. `above` puts the menu's
        bottom at `position` instead of its top."""
        menu = self._menus.for_entry(info)
        if above:
            position = QPoint(position.x(), position.y() - menu.sizeHint().height())
        menu.exec(position)

    # File operations

    def _selected_locations(self):
        return self._pane.selected_locations()

    def copy(self):
        self._file_actions.to_clipboard(cut=False)

    def cut(self):
        self._file_actions.to_clipboard(cut=True)

    def paste(self):
        """Copy what is on the clipboard into this folder, or move it if it was cut."""
        return self._file_actions.paste()

    def duplicate(self):
        """A copy of each selected item beside itself, named as "Keep both" names it."""
        return self._file_actions.duplicate()

    def create_link(self):
        """A symbolic link to each selected item, beside it."""
        return self._file_actions.create_link()

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
        return self._file_actions.trash()

    def delete_selected(self):
        """Delete for good. The job asks for confirmation before it touches anything."""
        return self._file_actions.delete()

    def restore_selected(self):
        """Put the selected trashed items back where they were."""
        return self._file_actions.restore()

    def empty_trash(self):
        """Delete everything in the trash for good. The job asks first, with the count."""
        return self._file_actions.empty_trash()

    def properties(self):
        """Show the properties of the selection, or of this folder when nothing is selected."""
        selected = self._selected_locations() or [self._location]
        dialog = PropertiesDialog(selected, self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.show()
        return dialog

    def set_permissions(self, preset):
        """Give the selection the permissions of `preset`, as a job."""
        return self._file_actions.set_permissions(preset)

    def copy_path(self):
        """Put the paths of the selection on the clipboard as text, one per line.
        With nothing selected, the path of this folder."""
        selected = self._selected_locations() or [self._location]
        clipboard.put_text("\n".join(locations.display_path(item) for item in selected))
        what = "Path" if len(selected) == 1 else f"{len(selected)} paths"
        self.statusBar().showMessage(f"{what} copied", _COPIED_MS)

    def terminal_folder(self, folder=None):
        """The path a terminal would start in, or None when it can't: no terminal
        installed, or a folder that is not on this computer's file systems."""
        folder = folder or self._location
        if folder is None or desktop.default_terminal() is None:
            return None
        return locations.local_path(folder)

    def open_terminal(self, folder=None):
        path = self.terminal_folder(folder)
        if path is not None:
            desktop.open_terminal(path, self.show_error)

    def move_selected(self):
        """Move the selection to a folder that is picked in a dialog."""
        return self._file_actions.move_to()

    # Archives

    def extract_here(self):
        """Extract the selected archives beside themselves, a job each."""
        return self._file_actions.extract_here()

    def extract_to(self):
        """Extract the selected archives into a folder that is picked in a dialog."""
        return self._file_actions.extract_to()

    def compress(self):
        """Compress the selection into an archive in this folder, after asking for its
        name and kind."""
        return self._file_actions.compress()

    def copy_to_other_pane(self):
        return self._file_actions.to_other_pane(JobKind.COPY)

    def move_to_other_pane(self):
        return self._file_actions.to_other_pane(JobKind.MOVE)

    def new_file(self):
        return self._file_actions.new_file()

    def new_folder(self):
        return self._file_actions.new_folder()

    def rename(self):
        return self._file_actions.rename()

    # Undo

    def _update_undo(self):
        what = self._jobs.undo_text()
        self._actions.undo.setText(f"Undo {what}" if what else "Undo")
        self._actions.undo.setEnabled(what is not None)

    def undo(self):
        """Take back the last rename, move, trash or new folder, of this window or of
        another. A move and a trash are taken back by a job, which is returned."""
        return self._file_actions.undo()

    def _on_dropped(self, uris, folder_info, modifiers, position):
        """Files were dropped on the list, or on a folder in it."""
        return self._file_actions.drop_on(self._pane, uris, folder_info, modifiers, position)

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
