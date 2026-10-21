"""What a window does to the files of its active pane when asked: a new file or folder,
a rename, links, what a drop does, paste, trash and delete, moves, archives and undo.
Made, named and linked at once, or handed to `JobModel` as a job.

What touches the file system runs on a worker (`workers.Background`), and what it comes
to is shown when the answer is in: a folder that does not answer, a network share say,
never holds up the window. A name dialog waits, busy, until its answer comes.

The window keeps the actions, menus and signals and calls these. It gives the pane to
work on, the job model, and where to say how it went.
"""

from types import SimpleNamespace

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QFileDialog
from shiboken6 import isValid

from filehouse.core import archives, locations
from filehouse.core.errors import OperationError
from filehouse.core.operations import JobKind, quick, undo
from filehouse.core.operations.conflicts import split_extension
from filehouse.integration import style
from filehouse.qt_models.workers import Background
from filehouse.ui import clipboard, menus
from filehouse.ui.dialogs.compress import CompressDialog
from filehouse.ui.dialogs.details import ErrorDetails
from filehouse.ui.dialogs.name import LATER, ask_name
from filehouse.ui.dialogs.rename_many import RenameManyDialog

# What a drop can be asked to do beside the two kinds of job
LINK = "link"


def answer_later(background, dialog, work, done, problem):
    """For a NameDialog's `apply`: do `work()` on `background`, then `done(result)` and
    the dialog's answer. `problem(reason)` says what went wrong. Returns LATER."""

    def finished(result):
        done(result)
        dialog.answer(None)

    def failed(error):
        dialog.answer(problem(_reason(error)))

    background.call(work, finished, failed)
    return LATER


def all_in(sources, folder):
    """Whether every one of `sources` is directly in `folder` already."""
    return all(locations.same(locations.parent(source), folder) for source in sources)


def pick_folder(parent, title, start):
    """Ask for a folder with the desktop's own dialog. Returns its path, or "" for none."""
    return QFileDialog.getExistingDirectory(parent, title, start)


def is_archive(info):
    """Whether `info` is a file that can be extracted."""
    return not info.is_dir and archives.can_extract(info.content_type)


def _start_folder(pane):
    """Where a folder dialog opens: the pane's folder when it is on this computer."""
    return locations.local_path(pane.location) or locations.local_path(locations.home())


def _reason(error):
    """Why a piece of work failed, to follow a colon."""
    if isinstance(error, OperationError):
        return error.message
    return f"internal error: {error}"


class FileActions(QObject):
    """`window` is the parent of the dialogs and the origin of the jobs; `pane()` gives
    the active pane, `other_pane()` the other one of a split tab or None, `say(text)` a
    short message of success, `show_error(text)` one of failure."""

    def __init__(self, window, jobs, pane, other_pane, say, show_error):
        super().__init__(window)
        self._window = window
        self._jobs = jobs
        self._pane = pane
        self._other_pane = other_pane
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
        return answer_later(self._background, dialog, work, done, problem)

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

    # The clipboard

    def to_clipboard(self, cut):
        selected = self._pane().selected_locations()
        if selected:
            clipboard.put([locations.uri(location) for location in selected], cut)

    def paste(self):
        """Copy what is on the clipboard into this folder, or move it if it was cut."""
        pane = self._pane()
        uris, cut = clipboard.get()
        if not uris or pane.read_only:
            return None
        sources = [locations.from_uri(uri) for uri in uris]
        if cut:
            # A cut is used up by its paste
            clipboard.clear()
            if all_in(sources, pane.location):
                return None
        return self._start(JobKind.MOVE if cut else JobKind.COPY, sources, pane.location)

    def duplicate(self):
        """A copy of each selected item beside itself, named as "Keep both" names it."""
        pane = self._pane()
        selected = pane.selected_locations()
        if not selected or pane.read_only:
            return None
        return self._start(JobKind.DUPLICATE, selected)

    # The trash

    def trash(self):
        pane = self._pane()
        if pane.in_trash:
            # Already there: the Delete key means delete for good, which asks first
            return self.delete()
        selected = pane.selected_locations()
        return self._start(JobKind.TRASH, selected) if selected else None

    def delete(self):
        """Delete for good. The job asks for confirmation before it touches anything."""
        pane = self._pane()
        if pane.in_recent:
            return None
        if pane.in_trash and not pane.at_trash:
            # The trash only deletes whole items, not what is inside a trashed folder
            return None
        selected = pane.selected_locations()
        return self._start(JobKind.DELETE, selected) if selected else None

    def restore(self):
        """Put the selected trashed items back where they were."""
        pane = self._pane()
        infos = [info for info in pane.view.selected_infos() if info.original_path is not None]
        if not infos:
            return None
        return self._jobs.start(
            JobKind.RESTORE,
            [locations.child(pane.location, info.name) for info in infos],
            origin=self._window,
            targets=[locations.from_path(info.original_path) for info in infos],
        )

    def empty_trash(self):
        """Delete everything in the trash for good. The job asks first, with the count."""
        pane = self._pane()
        model = pane.model
        if not pane.at_trash or not model.rowCount():
            return None
        everything = [
            locations.child(pane.location, model.info(row).name) for row in range(model.rowCount())
        ]
        return self._start(JobKind.DELETE, everything)

    # Elsewhere

    def set_permissions(self, preset):
        """Give the selection the permissions of `preset`, as a job. Only the items
        themselves: nothing inside a selected folder is changed."""
        pane = self._pane()
        selected = pane.selected_locations()
        if not selected or pane.read_only:
            return None
        return self._jobs.set_permissions(preset, selected, origin=self._window)

    def move_to(self):
        """Move the selection to a folder that is picked in a dialog."""
        pane = self._pane()
        selected = pane.selected_locations()
        if not selected or pane.read_only:
            return None
        path = pick_folder(self._window, "Move to", _start_folder(pane))
        if not path:
            return None
        target = locations.from_path(path)
        if all_in(selected, target):
            # Already there
            return None
        return self._start(JobKind.MOVE, selected, target)

    def to_other_pane(self, kind):
        """Copy or move the selection into the folder of the other pane."""
        pane = self._pane()
        other = self._other_pane()
        selected = pane.selected_locations()
        if other is None or not selected or pane.in_trash or other.read_only:
            return None
        if kind is JobKind.MOVE and pane.read_only:
            return None
        if kind is JobKind.MOVE and all_in(selected, other.location):
            # Already there
            return None
        return self._start(kind, selected, other.location)

    # Archives

    def selected_archives(self):
        pane = self._pane()
        return [
            locations.entry_location(pane.location, info)
            for info in pane.view.selected_infos()
            if is_archive(info)
        ]

    def extract_here(self):
        """Extract the selected archives beside themselves, a job each."""
        pane = self._pane()
        if pane.read_only or pane.in_trash:
            return []
        return self._extract(pane.location)

    def extract_to(self):
        """Extract the selected archives into a folder that is picked in a dialog."""
        pane = self._pane()
        if not self.selected_archives() or pane.in_trash:
            return []
        path = pick_folder(self._window, "Extract to", _start_folder(pane))
        return self._extract(locations.from_path(path)) if path else []

    def _extract(self, destination):
        return [
            self._jobs.extract(archive, destination, origin=self._window)
            for archive in self.selected_archives()
        ]

    def compress(self):
        """Compress the selection into an archive in this folder, after asking for its
        name and kind."""
        pane = self._pane()
        selected = pane.selected_locations()
        if not selected or pane.read_only or not archives.available():
            return None
        folder = pane.location
        names = [locations.basename(location) for location in selected]
        what = f"“{names[0]}”" if len(names) == 1 else f"{len(names)} items"

        def apply(text, format):
            name = text.strip()
            if not name or "/" in name or name in (".", ".."):
                return "The archive needs a name, without “/”."
            extension = archives.format_for(format).extension
            if not name.endswith(extension):
                name += extension
            self._jobs.compress(selected, folder, name, format, origin=self._window)
            return None

        dialog = CompressDialog(
            self._window,
            what,
            archives.FORMATS,
            lambda format: archives.archive_name(names, locations.basename(folder), format),
            apply,
        )
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.open()
        return dialog

    # Undo

    def undo(self):
        """Take back the last rename, move, trash or new folder, of this window or of
        another. A move and a trash are taken back by a job, which says how it went
        as any job does, and returned; the others on a worker, and what could not be
        done is named once they are."""
        return self._jobs.undo(origin=self._window, done=self._on_undone)

    def _on_undone(self, undone):
        if not isValid(self):
            # Closed meanwhile: what was taken back stays taken back
            return
        what = undone.record.describe()
        errors = list(undone.errors)
        if undone.changed:
            pane = self._pane()
            pane.search_again()
            if len(undone.changed) == 1 and undone.record.kind is undo.Kind.RENAME:
                now = undone.changed[0]
                pane.select_when_listed(locations.parent(now), locations.basename(now))
        if not errors:
            self._say(f"Undid the {what}")
        elif len(errors) == 1:
            self._show_error(f"Could not undo the {what}: {errors[0]}.")
        else:
            # Every one of them, by name
            summary = f"Could not undo the {what} for {len(errors)} of them."
            self._show_error(summary)
            ErrorDetails(SimpleNamespace(errors=errors, summary=summary), self._window).open()
