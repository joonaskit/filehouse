"""Planning: expanding a request into a flat list of steps, with sizes.

Planning reads and never changes anything. What it learns about the
destination is advisory: every step checks again when it runs.
"""

import os
from dataclasses import dataclass, field
from enum import Enum

from gi.repository import Gio, GLib

from filehouse.core import locations
from filehouse.core.errors import OperationError, is_cancelled

_ATTRIBUTES = "standard::name,standard::type,standard::size,id::filesystem"
_NOFOLLOW = Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS
_DIRECTORY = Gio.FileType.DIRECTORY


class StepKind(Enum):
    MAKE_DIR = "create folder"
    COPY = "copy"  # a file or a link; in a move, the source is removed afterwards
    RENAME = "move"  # a whole folder moved in one go on the same file system
    FINISH_DIR = "finish folder"  # after a folder's contents: metadata, and in a move the source
    TRASH = "trash"
    RESTORE = "restore"  # a trashed item back to where it was, in one go
    DELETE = "delete"
    UNPLANNED = "read"  # planning failed here with `error`; running it plans again


@dataclass(eq=False)
class Step:
    kind: StepKind
    source: Gio.File
    # Where it lands: `name` in the folder made by `parent`, or in `folder` at the top level.
    # For a delete, `parent` is the step that removes the folder this item is in.
    parent: "Step | None" = None
    folder: Gio.File | None = None
    name: str = ""
    size: int = 0
    is_dir: bool = False
    counted: bool = False  # one of the "N of M" items
    whole: bool = False  # a delete of the item and all that is in it: what is in the trash
    existing: Gio.FileType | None = None  # found at the destination while planning
    error: GLib.Error | None = None

    # Set while running
    done: bool = False
    failed: bool = False
    left_out: bool = False  # the user skipped it in a conflict
    overwrite: bool = False  # the user chose Replace; for a folder, merge
    fresh: bool = False  # renamed by Keep both, so nothing beneath it can conflict
    created: bool = False  # this step made the destination folder
    copied: bool = False  # the copy is complete, only the source is left to remove
    incomplete: bool = False  # something inside failed
    children: list = field(default_factory=list, repr=False)  # the steps that name it `parent`

    def __post_init__(self):
        if self.parent is not None:
            self.parent.children.append(self)

    @property
    def dest(self):
        step = self.parent if self.kind is StepKind.FINISH_DIR else self
        # Up to the top-level step and down again, not a call for each folder above
        names = [step.name]
        while step.parent is not None:
            step = step.parent
            names.append(step.name)
        location = step.folder
        for name in reversed(names):
            location = location.get_child(name)
        return location

    def ancestors(self):
        step = self.parent
        while step is not None:
            yield step
            step = step.parent

    def blocked(self):
        """Not to be run: the folder it belongs in failed or was left out."""
        return any(step.failed or step.left_out for step in self.ancestors())

    def excluded(self):
        """Left out on purpose, so it doesn't count towards the totals."""
        return self.left_out or any(step.left_out for step in self.ancestors())

    def under_fresh(self):
        return any(step.fresh for step in self.ancestors())

    def merges(self):
        """A folder that may be made where one exists: merged folders merge all the way down."""
        return self.overwrite or (self.parent is not None and self.parent.overwrite)


def check_not_inside(source, folder, verb):
    """Raise when `folder` is `source` or lies inside it."""
    try:
        if locations.file_type(source) != _DIRECTORY:
            return
    except GLib.Error:
        return
    real_source, real_folder = _real(source), _real(folder)
    if real_folder.equal(real_source) or real_folder.has_prefix(real_source):
        raise OperationError.at(source, verb, f"cannot {verb} a folder into itself")


def plan_transfer(source, folder, name, move, cancellable, parent=None, rename=True):
    """The steps that copy or move `source` to `name` in `folder` (under `parent` when nested)."""
    steps = []
    _walk(steps, source, parent, folder, name, move, cancellable, rename)
    return steps


def plan_delete(source, cancellable, parent=None, walk=False):
    """The steps that delete `source`: contents first, each folder after what is in it.

    Something in the trash is one step: the trash deletes an item with all that is in
    it, and nothing inside a trashed folder can be deleted by itself. `walk` plans it
    file by file all the same."""
    if parent is None and not walk and locations.in_trash(source):
        return [Step(StepKind.DELETE, source, counted=True, whole=True)]
    steps = []
    _walk_delete(steps, source, parent, cancellable)
    return steps


def plan_trash(source):
    return [Step(StepKind.TRASH, source, counted=True)]


def plan_restore(source, folder, name, cancellable):
    """One step that moves a trashed item to `name` in `folder`. The trash moves a folder whole."""
    try:
        info = source.query_info(_ATTRIBUTES, _NOFOLLOW, cancellable)
        existing = locations.file_type(folder.get_child(name), cancellable)
    except GLib.Error as error:
        if is_cancelled(error):
            raise
        return [Step(StepKind.UNPLANNED, source, folder=folder, name=name, error=error)]
    return [
        Step(
            StepKind.RESTORE,
            source,
            folder=folder,
            name=name,
            is_dir=info.get_file_type() == _DIRECTORY,
            counted=True,
            existing=existing,
        )
    ]


def _walk(steps, source, parent, folder, name, move, cancellable, rename):
    """Add the steps for `source` and everything in it: a folder's MAKE_DIR, what is in
    it, then its FINISH_DIR. From a list of what is left to plan rather than a call for
    each folder, so that no tree is too deep to plan."""
    # What to plan, as the arguments of `_plan_one`, or the step that finishes a folder
    pending = [(source, None, parent, folder, name, True, rename)]
    while pending:
        item = pending.pop()
        if isinstance(item, Step):
            steps.append(item)
            continue
        planned = _plan_one(steps, *item, move=move, cancellable=cancellable)
        if planned is None:
            continue
        made, children, merging = planned
        pending.append(Step(StepKind.FINISH_DIR, made.source, parent=made, is_dir=True))
        # Last first, so they come off the list in order
        for child in reversed(children):
            child_name = child.get_name()
            pending.append(
                (made.source.get_child(child_name), child, made, None, child_name, merging, False)
            )


def _plan_one(steps, source, info, parent, folder, name, look, rename, *, move, cancellable):
    """Add the step for `source` itself. For a folder whose contents go item by item,
    returns its MAKE_DIR, what is in it, and whether it may be merged into one that is
    there; None otherwise."""
    mark = len(steps)
    try:
        if info is None:
            info = source.query_info(_ATTRIBUTES, _NOFOLLOW, cancellable)
        base = parent.dest if parent is not None else folder
        # Nothing can be in the way inside a folder that doesn't exist yet
        existing = locations.file_type(base.get_child(name), cancellable) if look else None
        common = {"parent": parent, "folder": folder, "name": name, "existing": existing}

        if info.get_file_type() != _DIRECTORY:
            size = info.get_size() if info.get_file_type() == Gio.FileType.REGULAR else 0
            steps.append(Step(StepKind.COPY, source, size=size, counted=True, **common))
            return None

        if move and rename and existing is None and _same_filesystem(info, base, cancellable):
            steps.append(Step(StepKind.RENAME, source, is_dir=True, counted=True, **common))
            return None

        children = _children(source, cancellable)
        made = Step(StepKind.MAKE_DIR, source, is_dir=True, counted=not children, **common)
        steps.append(made)
        return made, children, existing == _DIRECTORY
    except GLib.Error as error:
        if is_cancelled(error):
            raise
        del steps[mark:]
        steps.append(
            Step(StepKind.UNPLANNED, source, parent=parent, folder=folder, name=name, error=error)
        )
        return None


def _walk_delete(steps, source, parent, cancellable):
    """Add the steps that delete `source`, each folder after what is in it. From a list
    of what is left to plan, as `_walk`."""
    # What to plan, as (source, info, parent), or the step that deletes a folder
    pending = [(source, None, parent)]
    while pending:
        item = pending.pop()
        if isinstance(item, Step):
            steps.append(item)
            continue
        source, info, parent = item
        try:
            if info is None:
                info = source.query_info(_ATTRIBUTES, _NOFOLLOW, cancellable)
            if info.get_file_type() != _DIRECTORY:
                steps.append(Step(StepKind.DELETE, source, parent=parent, counted=True))
                continue
            children = _children(source, cancellable)
        except GLib.Error as error:
            if is_cancelled(error):
                raise
            steps.append(Step(StepKind.UNPLANNED, source, parent=parent, error=error))
            continue
        folder = Step(StepKind.DELETE, source, parent=parent, is_dir=True, counted=not children)
        pending.append(folder)
        for child in reversed(children):
            pending.append((source.get_child(child.get_name()), child, folder))


def _children(folder, cancellable):
    """The entries of a folder, links not followed, in a stable order."""
    enumerator = folder.enumerate_children(_ATTRIBUTES, _NOFOLLOW, cancellable)
    infos = []
    try:
        while (info := enumerator.next_file(cancellable)) is not None:
            infos.append(info)
    finally:
        try:
            enumerator.close(None)
        except GLib.Error:
            pass
    return sorted(infos, key=lambda info: info.get_name())


def _same_filesystem(info, folder, cancellable):
    try:
        other = folder.query_info("id::filesystem", Gio.FileQueryInfoFlags.NONE, cancellable)
    except GLib.Error as error:
        if is_cancelled(error):
            raise
        return False
    ours = info.get_attribute_string("id::filesystem")
    return ours is not None and ours == other.get_attribute_string("id::filesystem")


def _real(location):
    path = location.get_path()
    return Gio.File.new_for_path(os.path.realpath(path)) if path else location
