"""What a window does to the files of its active pane when asked: a new file or folder,
a rename, links, and what a drop does. Made, named and linked at once, or handed to
`JobModel` as a job.

The window keeps the actions, menus and signals and calls these. It gives the pane to
work on, the job model, and where to say how it went.
"""

from types import SimpleNamespace

from PySide6.QtCore import QObject, Qt

from filehouse.core import locations
from filehouse.core.errors import OperationError
from filehouse.core.operations import JobKind, quick, undo
from filehouse.core.operations.conflicts import split_extension
from filehouse.integration import style
from filehouse.ui import menus
from filehouse.ui.dialogs.details import ErrorDetails
from filehouse.ui.dialogs.name import ask_name
from filehouse.ui.dialogs.rename_many import RenameManyDialog

# What a drop can be asked to do beside the two kinds of job
LINK = "link"


def all_in(sources, folder):
    """Whether every one of `sources` is directly in `folder` already."""
    return all(locations.same(locations.parent(source), folder) for source in sources)


class FileActions(QObject):
    """`window` is the parent of the dialogs and the origin of the jobs; `pane()` gives
    the active pane, `say(text)` a short message of success, `show_error(text)` one of
    failure."""

    def __init__(self, window, jobs, pane, say, show_error):
        super().__init__(window)
        self._window = window
        self._jobs = jobs
        self._pane = pane
        self._say = say
        self._show_error = show_error

    def _start(self, kind, sources, destination=None):
        return self._jobs.start(kind, sources, destination, origin=self._window)

    # Making

    def new_file(self):
        pane = self._pane()
        if pane.read_only:
            return None
        folder = pane.location
        name = quick.suggest_file_name(folder)

        def create(text):
            try:
                made = quick.create_file(folder, text)
            except OperationError as error:
                return f"Could not create the file: {error.message}."
            self._jobs.remember(undo.created(made))
            pane.select_when_listed(folder, locations.basename(made))
            return None

        return ask_name(self._window, "New file", "Name of the new file:", name, create)

    def new_folder(self):
        pane = self._pane()
        if pane.read_only:
            return None
        folder = pane.location
        name = quick.suggest_folder_name(folder)

        def create(text):
            try:
                made = quick.create_folder(folder, text)
            except OperationError as error:
                return f"Could not create the folder: {error.message}."
            self._jobs.remember(undo.created(made))
            pane.select_when_listed(folder, locations.basename(made))
            return None

        return ask_name(self._window, "New folder", "Name of the new folder:", name, create)

    # Renaming

    def rename(self):
        pane = self._pane()
        infos = pane.view.selected_infos()
        if not infos or pane.read_only:
            return None
        if len(infos) > 1:
            return self._rename_many(pane, infos)
        info = infos[0]
        folder = pane.location
        target = locations.child(folder, info.name)

        def apply(text):
            try:
                renamed = quick.rename(target, text)
            except OperationError as error:
                return f"Could not rename “{info.display_name}”: {error.message}."
            self._jobs.remember(undo.renamed([(renamed, target)]))
            pane.select_when_listed(folder, locations.basename(renamed))
            # Nothing watches the folders below: a result that was renamed is found again
            pane.search_again()
            return None

        # Not `info.name`: for a result of a search that is its path below this folder
        name = locations.basename(target)
        stem = name if info.is_dir else split_extension(name)[0]
        return ask_name(
            self._window,
            "Rename",
            f"New name for “{info.display_name}”:",
            name,
            apply,
            len(stem),
        )

    def _rename_many(self, pane, infos):
        """Several at once, in a dialog that shows every new name first."""
        folder = pane.location
        before = [locations.child(folder, info.name) for info in infos]

        def renamed(now):
            # In the order of the list, as they were given
            self._jobs.remember(undo.renamed(zip(now, before, strict=True)))
            names = {locations.name_below(folder, location) for location in now}
            pane.select_names_when_listed(folder, names)
            # Nothing watches the folders below: results that were renamed are found again
            pane.search_again()
            self._say(f"{len(now)} items renamed")

        dialog = RenameManyDialog(
            self._window,
            [(location, info.is_dir) for location, info in zip(before, infos, strict=True)],
            renamed,
        )
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.open()
        return dialog

    # Links

    def create_link(self):
        """A symbolic link to each selected item, beside it."""
        pane = self._pane()
        selected = pane.selected_locations()
        if not selected or pane.read_only:
            return []
        # Each beside what it points to, which for a result of a search is further down
        made = []
        errors = []
        for target in selected:
            links, failed = quick.create_links([target], locations.parent(target))
            made += links
            errors += failed
        return self._after_linking(pane, made, errors, len(selected), pane.location)

    def _link_into(self, sources, folder):
        """A link in `folder` to each of the files dropped there."""
        made, errors = quick.create_links(sources, folder)
        return self._after_linking(self._pane(), made, errors, len(sources), folder)

    def _after_linking(self, pane, made, errors, count, folder):
        if len(made) == 1:
            pane.select_when_listed(folder, locations.basename(made[0]))
        if not errors:
            self._say("Link created" if count == 1 else f"{count} links created")
        elif len(errors) == 1:
            self._show_error(f"Could not create a link to {errors[0]}.")
        else:
            # Every one of them, by name
            summary = f"Could not create {len(errors)} of {count} links."
            self._show_error(summary)
            ErrorDetails(SimpleNamespace(errors=errors, summary=summary), self._window).open()
        return made

    # Drops

    def drop_on(self, pane, uris, folder_info, modifiers, position):
        """Files were dropped on `pane`'s list, on a folder in it (`folder_info`), or on
        the tab that shows it."""
        if pane.read_only and not pane.at_trash:
            # Nothing is put into the recent files, and not into a trashed folder
            return None
        target = pane.location
        if folder_info is not None:
            target = locations.child(target, folder_info.name)
        # The trash itself takes what is dropped on it, and nothing inside it does
        trash_ok = pane.at_trash and folder_info is None
        return self.drop_into(target, uris, modifiers, position, trash_ok)

    def drop_into(self, target, uris, modifiers, position, trash_ok=False):
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
            kind = self.ask_drop_action(position)
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
        if kind is JobKind.MOVE and all_in(sources, target):
            # Already where they were dropped
            return None
        return self._start(kind, sources, target)

    def ask_drop_action(self, position):
        """Move, copy or link, from a menu at `position`; None when it is cancelled."""
        menu = menus.menu(self._window)
        move = menu.addAction(
            style.icon("go-jump-symbolic", "transform-move", menu=True), "Move here"
        )
        copy = menu.addAction(style.icon("edit-copy-symbolic", "edit-copy", menu=True), "Copy here")
        link = menu.addAction(style.icon("edit-link-symbolic", "edit-link", menu=True), "Link here")
        menu.addSeparator()
        menu.addAction("Cancel")
        return {move: JobKind.MOVE, copy: JobKind.COPY, link: LINK}.get(menu.exec(position))
