"""What a window does to the files of its active pane when asked: a new file or folder,
a rename, links, and what a drop does. Made, named and linked at once, or handed to
`JobModel` as a job.

What touches the file system runs on a worker (`workers.Background`), and what it comes
to is shown when the answer is in: a folder that does not answer, a network share say,
never holds up the window. A name dialog waits, busy, until its answer comes.

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
from filehouse.qt_models.workers import Background
from filehouse.ui import menus
from filehouse.ui.dialogs.details import ErrorDetails
from filehouse.ui.dialogs.name import LATER, ask_name
from filehouse.ui.dialogs.rename_many import RenameManyDialog

# What a drop can be asked to do beside the two kinds of job
LINK = "link"


def all_in(sources, folder):
    """Whether every one of `sources` is directly in `folder` already."""
    return all(locations.same(locations.parent(source), folder) for source in sources)


def _reason(error):
    """Why a piece of work failed, to follow a colon."""
    if isinstance(error, OperationError):
        return error.message
    return f"internal error: {error}"


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
        self._background = Background(self)

    @property
    def busy(self):
        """Whether something asked for is still being done."""
        return self._background.busy

    def _start(self, kind, sources, destination=None):
        return self._jobs.start(kind, sources, destination, origin=self._window)

    def _asking(self, dialog, work, done, problem):
        """Do `work()` on the worker for a name `dialog` asked for, then `done(result)`
        and an answer for the dialog. `problem(message)` says what went wrong."""

        def finished(result):
            done(result)
            dialog.answer(None)

        def failed(error):
            dialog.answer(problem(_reason(error)))

        self._background.call(work, finished, failed)
        return LATER

    # Making

    def new_file(self):
        return self._new(
            quick.NEW_FILE,
            quick.suggest_file_name,
            quick.create_file,
            "New file",
            "Name of the new file:",
            "Could not create the file",
        )

    def new_folder(self):
        return self._new(
            quick.NEW_FOLDER,
            quick.suggest_folder_name,
            quick.create_folder,
            "New folder",
            "Name of the new folder:",
            "Could not create the folder",
        )

    def _new(self, name, suggest, create, title, label, failure):
        pane = self._pane()
        if pane.read_only:
            return None
        folder = pane.location

        def made(location):
            self._jobs.remember(undo.created(location))
            pane.select_when_listed(folder, locations.basename(location))

        def apply(text):
            return self._asking(
                dialog, lambda: create(folder, text), made, lambda reason: f"{failure}: {reason}."
            )

        # The plain name at once; "New folder (2)" once it is known whether that is taken
        dialog = ask_name(self._window, title, label, name, apply)
        self._background.call(lambda: suggest(folder), dialog.suggest)
        return dialog

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

        def renamed(now):
            self._jobs.remember(undo.renamed([(now, target)]))
            pane.select_when_listed(folder, locations.basename(now))
            # Nothing watches the folders below: a result that was renamed is found again
            pane.search_again()

        def apply(text):
            return self._asking(
                dialog,
                lambda: quick.rename(target, text),
                renamed,
                lambda reason: f"Could not rename “{info.display_name}”: {reason}.",
            )

        # Not `info.name`: for a result of a search that is its path below this folder
        name = locations.basename(target)
        stem = name if info.is_dir else split_extension(name)[0]
        dialog = ask_name(
            self._window,
            "Rename",
            f"New name for “{info.display_name}”:",
            name,
            apply,
            len(stem),
        )
        return dialog

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
            self._background,
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
            return

        def link():
            # Each beside what it points to, which for a result of a search is further down
            made, errors = [], []
            for target in selected:
                links, failed = quick.create_links([target], locations.parent(target))
                made += links
                errors += failed
            return made, errors

        self._background.call(
            link, lambda result: self._after_linking(pane, *result, len(selected), pane.location)
        )

    def _link_into(self, sources, folder):
        """A link in `folder` to each of the files dropped there."""
        pane = self._pane()
        self._background.call(
            lambda: quick.create_links(sources, folder),
            lambda result: self._after_linking(pane, *result, len(sources), folder),
        )

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

    # Drops

    def drop_on(self, pane, uris, folder_info, modifiers, position):
        """Files were dropped on `pane`'s list, on a folder in it (`folder_info`), or on
        the tab that shows it."""
        if pane.read_only and not pane.at_trash:
            # Nothing is put into the recent files, and not into a trashed folder
            return
        target = pane.location
        if folder_info is not None:
            target = locations.child(target, folder_info.name)
        # The trash itself takes what is dropped on it, and nothing inside it does
        trash_ok = pane.at_trash and folder_info is None
        self.drop_into(target, uris, modifiers, position, trash_ok)

    def drop_into(self, target, uris, modifiers, position, trash_ok=False):
        """Move or copy dropped files into a folder, by the keys held and the file systems.
        What it starts is a job of the job model, or links, once they are made."""
        if locations.in_recent(target):
            return
        sources = [locations.from_uri(uri) for uri in uris]
        if any(locations.same(source, target) for source in sources):
            # A folder dropped on itself
            return
        if locations.in_trash(target):
            # Dropped on the trash: that is where they go. Not into a trashed folder
            sources = [source for source in sources if not locations.in_trash(source)]
            if trash_ok and sources:
                self._start(JobKind.TRASH, sources)
            return

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
        else:
            # As in Dolphin: a drop moves within a file system and copies across
            self._background.call(
                lambda: all(locations.same_filesystem(source, target) for source in sources),
                lambda same: self._drop_as(JobKind.MOVE if same else JobKind.COPY, sources, target),
            )
            return
        self._drop_as(kind, sources, target)

    def _drop_as(self, kind, sources, target):
        if kind is None:
            return
        if kind == LINK:
            self._link_into(sources, target)
        elif kind is not JobKind.MOVE or not all_in(sources, target):
            # A move to where they are already is nothing to do
            self._start(kind, sources, target)

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
