"""The actions of a window: what each is called, its icon and keys, and when it can be
used. The window says what each one does, puts them on its buttons and in its menus,
and tells them when what they are about changes (`update`).

Every action is an attribute, by the name in `_ACTIONS`: `actions.copy`, `actions.undo`.
"""

from PySide6.QtGui import QAction, QActionGroup, QKeySequence

from filehouse.core import archives, locations
from filehouse.integration import style
from filehouse.ui import clipboard, settings
from filehouse.ui.file_actions import is_archive

_KEYS = QKeySequence.StandardKey

# (name, text, icon, keys, colour). The keys are the strings of QKeySequence, or a
# standard key for the platform's whole list of them
_ACTIONS = (
    # Backspace goes back, as in Dolphin
    ("back", "Back", "go-previous", ["Alt+Left", "Backspace"], None),
    ("forward", "Forward", "go-next", ["Alt+Right"], None),
    ("up", "Up", "go-up", ["Alt+Up"], None),
    ("edit_location", "Edit location", "edit-entry", ["Ctrl+L"], None),
    ("reload", "Reload", "view-refresh", ["F5"], None),
    # The tab, or the window when it is the last tab
    ("close", "Close", "window-close", ["Ctrl+W"], None),
    ("new_tab", "New tab", "tab-new", ["Ctrl+T"], None),
    ("next_tab", "Next tab", "go-next", ["Ctrl+Tab", "Ctrl+PgDown"], None),
    ("previous_tab", "Previous tab", "go-previous", ["Ctrl+Shift+Tab", "Ctrl+PgUp"], None),
    ("hidden", "Show hidden files", "view-hidden", ["Ctrl+H"], None),
    # For the user who does not want the desktop to follow what they open. A setting
    # of the app, not of a window, as the browsers' private mode is not either
    ("private", "Don't remember opened files", "view-private", [], None),
    # The same keys as Dolphin
    ("icons_mode", "Icons", "view-list-icons", ["Ctrl+1"], None),
    ("details_mode", "Details", "view-list-details", ["Ctrl+3"], None),
    ("split_view", "Split view", "view-split-left-right", ["F3"], None),
    # Whether a wide window has the pane of details. The narrow window's details are
    # a sheet, and have a button of their own
    ("pane_toggle", "Details pane", "view-right-close", ["F11"], None),
    ("copy", "Copy", "edit-copy", _KEYS.Copy, None),
    # Not the platform's whole list for Cut: it includes Shift+Delete
    ("cut", "Cut", "edit-cut", ["Ctrl+X"], None),
    ("paste", "Paste", "edit-paste", _KEYS.Paste, None),
    ("rename", "Rename…", "edit-rename", ["F2"], None),
    # The last rename, move, trash or new folder of any window: see JobModel.undo
    ("undo", "Undo", "edit-undo", ["Ctrl+Z"], None),
    # A copy beside the file, and a link to it. Neither has a key: Ctrl+D is the
    # favourites', and Dolphin has none for them either
    ("duplicate", "Duplicate", "edit-duplicate", [], None),
    ("link", "Create link", "edit-link", [], None),
    ("trash", "Move to trash", "user-trash", ["Delete"], "danger"),
    ("delete", "Delete permanently…", "edit-delete", ["Shift+Delete"], "danger"),
    ("new_folder", "New folder…", "folder-new", ["Ctrl+Shift+N"], None),
    ("new_file", "New file…", "document-new", [], None),
    ("move_to", "Move to…", "document-export", [], None),
    # Archives: nothing is offered when libarchive is not installed
    ("extract_here", "Extract here", "archive-extract", [], None),
    ("extract_to", "Extract to…", "archive-extract", [], None),
    ("compress", "Compress…", "archive-insert", [], None),
    # Beside the other list, for the keyboard. Shift: F5 is Reload
    ("copy_other", "Copy to other pane", "edit-copy", ["Shift+F5"], None),
    ("move_other", "Move to other pane", "document-export", ["Shift+F6"], None),
    # The keys Dolphin has for these two
    ("copy_path", "Copy path", "edit-copy-path", ["Ctrl+Alt+C"], None),
    ("terminal", "Open terminal here", "utilities-terminal", ["Shift+F4"], None),
    ("select_all", "Select all", "edit-select-all", _KEYS.SelectAll, None),
    # The keys Dolphin has for these two
    ("select_matching", "Select matching…", "edit-select", ["Ctrl+S"], None),
    ("invert", "Invert selection", "edit-select-invert", ["Ctrl+Shift+A"], None),
    ("restore", "Restore", "edit-undo", [], None),
    ("empty_trash", "Empty trash…", "trash-empty", [], None),
    ("properties", "Properties", "document-properties", ["Alt+Return"], None),
    # For the narrow window. Neither has a key of its own
    ("show_details", "Details", "documentinfo", [], None),
    ("close_details", "Close details", "window-close", ["Escape"], None),
    # Space opens a larger look at the selection, and Space or Escape close it
    ("preview", "Preview", "view-preview", ["Space"], None),
    # For the preview as a sheet, which takes no keys itself. Only while it is open,
    # and the page keys only for a document: they are the list's otherwise
    ("close_preview_sheet", "Close preview", "window-close", ["Escape"], None),
    ("next_page", "Next page", "go-down", ["PgDown"], None),
    ("previous_page", "Previous page", "go-up", ["PgUp"], None),
    # In the recent files the Delete key takes the entry off the list
    ("forget", "Remove from recent files", "edit-clear-history", ["Delete"], None),
    ("clear_recent", "Clear recent files…", "edit-clear-history", [], None),
    # The folder open here, or the one a menu is for, in the bar under the toolbar
    ("favourite", "Add to favourites", "bookmark-new", ["Ctrl+D"], None),
    ("permissions", "Permissions", "object-locked", [], None),
    # The rules of the folder open here (#87), and running them now after a preview
    ("folder_rules", "Rules for this folder…", "view-filter", [], None),
    ("run_rules", "Run rules now…", "system-run", [], None),
    ("settings", "Settings", "preferences-system", ["Ctrl+,"], None),
    ("folder_menu", "Folder menu", "view-more-horizontal", [], None),
    ("search", "Search", "edit-find", ["Ctrl+F"], None),
)

# Turned on and off, not just done
_CHECKABLE = (
    "hidden",
    "private",
    "icons_mode",
    "details_mode",
    "split_view",
    "pane_toggle",
    "show_details",
)

# Only while what they are for is open
_OFF_AT_FIRST = ("close_details", "close_preview_sheet", "next_page", "previous_page", "forget")


def _shortcuts(keys):
    if isinstance(keys, QKeySequence.StandardKey):
        return QKeySequence.keyBindings(keys)
    return [QKeySequence(key) for key in keys]


class Actions:
    """`window` holds the actions, so their keys work anywhere in it. `does` maps the
    name of each action to what its `triggered` calls; one it leaves out is connected
    by the window itself."""

    def __init__(self, window, does):
        self._icons = []  # (action, icon name, colour) to make them again
        self.on_buttons = []  # the ones a button shows, not only a menu
        for name, text, icon, keys, colour in _ACTIONS:
            item = QAction(text, window)
            item.setShortcuts(_shortcuts(keys))
            if name in does:
                item.triggered.connect(does[name])
            item.setCheckable(name in _CHECKABLE)
            item.setEnabled(name not in _OFF_AT_FIRST)
            window.addAction(item)
            # Its icon comes with all the others, once it is known which are on buttons
            self._icons.append((item, icon, colour or "textPrimary"))
            setattr(self, name, item)
        modes = QActionGroup(window)
        modes.addAction(self.icons_mode)
        modes.addAction(self.details_mode)
        self.details_mode.setChecked(True)

    def draw_icons(self):
        """The icon of every action, in the colours of the theme in use. Most are shown
        by menus only and are made in a menu's size: every size of every icon was most
        of the time the window took to open."""
        for item, icon, colour in self._icons:
            menu = item not in self.on_buttons
            item.setIcon(style.icon(icon + "-symbolic", icon, colour=colour, menu=menu))

    def update(self, pane, infos, other, terminal):
        """What can be done now, in `pane` with `infos` selected. `other` is the other
        pane of a split tab or None; `terminal` whether a terminal can open here."""
        selected = len(infos)
        in_trash = pane.in_trash
        at_trash = pane.at_trash
        # The trash and the recent files are for looking at, and for what is said below
        read_only = pane.read_only
        self.copy.setEnabled(selected > 0)
        self.properties.setEnabled(True)
        # What is in the trash can be restored or deleted, and nothing else
        self.cut.setEnabled(selected > 0 and not read_only)
        # In the trash the Delete key deletes for good (and asks first, as always)
        for item in (self.trash, self.delete):
            item.setEnabled(selected > 0 and (at_trash or not read_only))
        # In the recent files it takes the entry off the list, not the file away
        self.forget.setEnabled(selected > 0 and pane.in_recent)
        self.clear_recent.setEnabled(pane.in_recent and pane.model.rowCount() > 0)
        self.rename.setEnabled(selected > 0 and not read_only)
        self.move_to.setEnabled(selected > 0 and not read_only)
        # Not of what is in the trash, and not into a list of what was used
        for item in (self.duplicate, self.link):
            item.setEnabled(selected > 0 and not read_only)
        has_archive = not in_trash and any(is_archive(info) for info in infos)
        self.extract_here.setEnabled(has_archive and not read_only)
        self.extract_to.setEnabled(has_archive)
        self.compress.setEnabled(selected > 0 and not read_only and archives.available())
        self.permissions.setEnabled(selected > 0 and not read_only)
        self.paste.setEnabled(bool(clipboard.get()[0]) and not read_only)
        # Copying what is in the recent files is copying the files; moving them is not
        # something a list of what was used should do
        self.copy_other.setEnabled(
            selected > 0 and not in_trash and other is not None and not other.read_only
        )
        self.move_other.setEnabled(
            selected > 0 and not read_only and other is not None and not other.read_only
        )
        for item in (self.new_folder, self.new_file):
            item.setEnabled(not read_only)
        # A terminal starts in a folder of this computer: not in the trash or a remote place
        self.terminal.setVisible(terminal)
        self.restore.setEnabled(
            selected > 0 and all(info.original_path is not None for info in infos)
        )
        self.empty_trash.setEnabled(at_trash and pane.model.rowCount() > 0)
        count = pane.proxy.rowCount()
        for item in (self.select_all, self.select_matching, self.invert):
            item.setEnabled(count > 0)
        self.preview.setEnabled(selected > 0)
        # Rules are for folders of this computer only (#87)
        local = not read_only and locations.local_path(pane.location) is not None
        self.folder_rules.setEnabled(local)
        self.run_rules.setVisible(
            local and bool(settings.folder_rules(locations.uri(pane.location)))
        )
