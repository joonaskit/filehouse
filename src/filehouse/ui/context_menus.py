"""The menus of a window: of an entry of its list or of the folder, of a place or a
favourite in the sidebar, and of the permissions. Each is made when it is asked for,
for the active pane as it is then, of the window's actions and of entries of its own.

What an entry of its own does, the window does: it is called back for it.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QCursor, QIcon

from filehouse.core import archives, locations
from filehouse.core.operations import permissions
from filehouse.integration import desktop, style
from filehouse.qt_models.directory_model import MODIFIED, NAME, SIZE
from filehouse.ui import menus, settings
from filehouse.ui.file_actions import is_archive

# What "Sort by" offers, with the order each starts in: the newest and the largest first
_SORTS = (
    ("Name", NAME, Qt.SortOrder.AscendingOrder),
    ("Modified", MODIFIED, Qt.SortOrder.DescendingOrder),
    ("Size", SIZE, Qt.SortOrder.DescendingOrder),
)


class ContextMenus:
    """`window` is the parent of the menus and does what their entries of its own ask
    for; `actions` are its `Actions`; `pane()` gives the active pane. `sidebar` ejects
    a drive, and `favourites` is the list of favourite folders, or None."""

    def __init__(self, window, actions, pane, sidebar, favourites):
        self._window = window
        self._actions = actions
        self._pane = pane
        self._sidebar = sidebar
        self._favourites = favourites

    def show_place(self, entry, position):
        """The menu of a place of the sidebar: a special folder or a drive."""
        menu = menus.menu(self._window)
        self._add_open(menu, entry.location)
        if getattr(entry, "can_eject", False):
            menu.addSeparator()
            menu.addAction(
                style.icon("media-eject-symbolic", "media-eject", menu=True),
                entry.eject_label,
                lambda: self._sidebar.eject(entry),
            )
        menu.exec(position)

    def _add_open(self, menu, location):
        menu.addAction(
            style.icon("document-open-symbolic", "document-open", menu=True),
            "Open",
            lambda: self._window.navigate(location),
        )
        menu.addAction(
            style.icon("tab-new-symbolic", "tab-new", menu=True),
            "Open in new tab",
            lambda: self._window.new_tab(location, activate=False),
        )
        if not self._window.is_narrow:
            menu.addAction(
                self._actions.split_view.icon(),
                "Open in split view",
                lambda: self._window.open_location_in_split(location),
            )

    def show_favourite(self, favourite, position):
        """The menu of a favourite of the sidebar."""
        menu = menus.menu(self._window)
        self._add_open(menu, favourite.location)
        menu.addSeparator()
        menu.addAction("Rename…", lambda: self._window.rename_favourite(favourite))
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

    def for_entry(self, info):
        """The menu of an entry of the active pane's list, or of its folder for None."""
        pane = self._pane()
        menu = menus.menu(self._window)
        if pane.in_trash:
            if info is not None:
                menu.addActions([self._actions.restore, self._actions.delete])
                menu.addSeparator()
                menu.addAction(self._actions.properties)
            else:
                menu.addAction(self._actions.empty_trash)
                menu.addActions(self._selecting())
                menu.addSeparator()
                self.add_sort_and_view(menu)
            return menu
        if pane.in_recent:
            return self._for_recent(menu, info)
        if info is None:
            # The folder's own menu
            menu.addActions([self._actions.new_folder, self._actions.new_file])
            menu.addSeparator()
            menu.addAction(self._actions.undo)
            menu.addAction(self._actions.paste)
            menu.addActions(self._selecting())
            menu.addSeparator()
            self.add_sort_and_view(menu)
            menu.addAction(self._actions.hidden)
            menu.addAction(self._actions.private)
            menu.addSeparator()
            menu.addAction(self._actions.favourite)
            menu.addActions([self._actions.copy_path, self._actions.terminal])
            self._add_open_in_manager(menu, pane.location)
            menu.addAction(self._actions.properties)
            return menu
        target = locations.child(pane.location, info.name)
        folder = pane.location
        menu.addAction(
            style.icon("document-open-symbolic", "document-open", menu=True),
            "Open",
            lambda: self._window.open_entry(info),
        )
        if info.folder:
            # A result of a search, from a folder below this one
            menu.addAction(
                style.icon("folder-open-symbolic", "folder-open", menu=True),
                "Show in folder",
                lambda: self._window.show_in_folder(info),
            )
        if info.is_dir:
            folder = target
            menu.addAction(
                style.icon("tab-new-symbolic", "tab-new", menu=True),
                "Open in new tab",
                lambda: self._window.open_in_new_tab(info),
            )
            if not self._window.is_narrow:
                menu.addAction(
                    self._actions.split_view.icon(),
                    "Open in split view",
                    lambda: self._window.open_in_split(info),
                )
        else:
            self._add_open_with(menu, info, target)
        self._add_open_in_manager(menu, folder)
        menu.addSeparator()
        if not info.is_dir:
            menu.addAction(self._actions.preview)
        menu.addActions([self._actions.cut, self._actions.copy, self._actions.paste])
        menu.addSeparator()
        menu.addActions(
            [
                self._actions.rename,
                self._actions.duplicate,
                self._actions.link,
                self._actions.move_to,
            ]
        )
        if is_archive(info) and not pane.in_trash:
            menu.addActions([self._actions.extract_here, self._actions.extract_to])
        if archives.available() and not pane.read_only:
            menu.addAction(self._actions.compress)
        if self._window.is_split:
            menu.addActions([self._actions.copy_other, self._actions.move_other])
        menu.addActions([self._actions.trash, self._actions.delete])
        menu.addSeparator()
        menu.addAction(self._select_type_action(menu, info))
        menu.addAction(self._actions.copy_path)
        if info.is_dir and self._favourites is not None:
            menu.addAction(
                self._actions.favourite.icon(),
                self._window.favourite_text(target),
                lambda: self._window.toggle_favourite(target),
            )
        if info.is_dir and self._window.terminal_folder(folder) is not None:
            # In the folder the menu is for, not the one it is listed in
            menu.addAction(
                self._actions.terminal.icon(),
                "Open terminal here",
                lambda: self._window.open_terminal(folder),
            )
        menu.addSeparator()
        menu.addMenu(self.permissions(menu))
        menu.addAction(self._actions.properties)
        return menu

    def _for_recent(self, menu, info):
        """What a file of the recent files can do: be opened, found, copied or looked at,
        and be taken off the list."""
        if info is None:
            menu.addActions(self._selecting())
            menu.addSeparator()
            self.add_sort_and_view(menu)
            menu.addAction(self._actions.private)
            menu.addSeparator()
            menu.addAction(self._actions.clear_recent)
            return menu
        target = locations.entry_location(self._pane().location, info)
        menu.addAction(
            style.icon("document-open-symbolic", "document-open", menu=True),
            "Open",
            lambda: self._window.open_entry(info),
        )
        if not info.is_dir:
            self._add_open_with(menu, info, target)
        menu.addAction(
            style.icon("folder-open-symbolic", "folder-open", menu=True),
            "Show in folder",
            lambda: self._window.show_in_folder(info),
        )
        menu.addSeparator()
        if not info.is_dir:
            menu.addAction(self._actions.preview)
        menu.addActions([self._actions.copy, self._actions.copy_path])
        menu.addAction(self._select_type_action(menu, info))
        menu.addSeparator()
        menu.addAction(self._actions.forget)
        menu.addAction(self._actions.properties)
        return menu

    def _selecting(self):
        """The folder menu's entries for what is selected."""
        return [self._actions.select_all, self._actions.select_matching, self._actions.invert]

    def _select_type_action(self, menu, info):
        item = QAction(self._actions.select_all.icon(), "Select all of this type", menu)
        item.triggered.connect(lambda: self._window.select_same_type(info))
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
                lambda app=app: self._window.open_with(app, target, info.content_type),
            )

    def permissions(self, parent):
        """The presets, for the selection: each with the mode it gives, and a tick
        on the one the selection has already."""
        menu = menus.menu(parent, "Permissions")
        menu.setIcon(self._actions.permissions.icon())
        infos = self._pane().view.selected_infos()
        kinds = {info.is_dir for info in infos}
        for preset in permissions.PRESETS:
            modes = {preset.mode_for(is_dir) for is_dir in kinds}
            text = preset.label
            if len(modes) == 1 and None not in modes:
                # Files and folders together get different modes: then the name says enough
                text += f"\t{next(iter(modes)):o}"
            item = menu.addAction(text, lambda preset=preset: self._window.set_permissions(preset))
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

    def show_permissions(self):
        """The presets in a menu of their own, where the pointer is."""
        pane = self._pane()
        if not pane.view.selected_infos() or pane.read_only:
            return
        self.permissions(self._window).exec(QCursor.pos())

    def _add_open_in_manager(self, menu, folder):
        manager = desktop.file_manager()
        if manager is not None:
            menu.addAction(
                style.icon("folder-open-symbolic", "folder-open", menu=True),
                f"Open folder in {manager.name}",
                lambda: desktop.open_with(manager, folder, self._window.show_error),
            )

    def add_sort_and_view(self, menu):
        """The folder menu's entries for how the list is shown."""
        pane = self._pane()
        shown = pane.view
        column, order = shown.sort_column(), shown.sort_order()
        sort = menus.menu(menu, "Sort by")
        sort.setIcon(style.icon("view-sort-symbolic", "view-sort", menu=True))
        for text, sorts_by, first_order in _SORTS:
            item = sort.addAction(
                text, lambda by=sorts_by, first=first_order: shown.sort(by, first)
            )
            item.setCheckable(True)
            item.setChecked(sorts_by == column)
        sort.addSeparator()
        for text, way in (
            ("Ascending", Qt.SortOrder.AscendingOrder),
            ("Descending", Qt.SortOrder.DescendingOrder),
        ):
            item = sort.addAction(text, lambda way=way: shown.sort(column, way))
            item.setCheckable(True)
            item.setChecked(way == order)
        menu.addMenu(sort)
        view = menus.menu(menu, "View")
        view.setIcon(style.icon("view-grid-symbolic", "view-grid", menu=True))
        view.addActions([self._actions.details_mode, self._actions.icons_mode])
        if settings.per_folder():
            # The view and the sort order that were chosen in this folder, given up
            default = view.addAction("Use the default here", pane.use_default_view)
            default.setEnabled(pane.has_own_view)
        view.addSeparator()
        view.addAction(self._actions.split_view)
        view.addAction(self._actions.pane_toggle)
        view.addAction(self._actions.new_tab)
        density = menus.menu(view, "Density")
        current = settings.density()
        for key, text in (("comfortable", "Comfortable"), ("compact", "Compact")):
            item = density.addAction(text, lambda key=key: self._window.set_density(key))
            item.setCheckable(True)
            item.setChecked(key == current)
        view.addMenu(density)
        menu.addMenu(view)
