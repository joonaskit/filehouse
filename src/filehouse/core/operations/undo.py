"""Undo: what an action did, kept so that it can be taken back.

A `Record` holds only what was finished, as it was finished: the files a move really
moved, the items a trash really trashed. Taking it back is an ordinary operation with
the safety rules of any other. Nothing is ever replaced, the world may have changed
since, and what cannot be put back is said by name.

- A move is taken back by a move job of each item to where it was (`Job.undo_of`).
- A trash is taken back by a restore job, which finds the items in the trash by where
  they were and when they went there.
- A rename is renamed back, and a new folder or file is removed if it is still empty:
  at once, like the actions themselves (`rename_back`, `remove_created`).

Not kept, because they cannot be taken back: a permanent delete, and whatever replaced
something, since what it replaced is gone.
"""

from dataclasses import dataclass
from enum import Enum

from gi.repository import Gio, GLib

from filehouse.core import locations
from filehouse.core.errors import OperationError, has_code
from filehouse.core.operations import rename_many
from filehouse.core.operations.plan import StepKind

_NOFOLLOW = Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS
# How many actions back an undo can go
LIMIT = 20


class Kind(Enum):
    RENAME = "rename"
    MOVE = "move"
    TRASH = "trash"
    CREATE = "create"


@dataclass(frozen=True)
class Record:
    """One action that can be taken back. `pairs` are (where it is now, where it was):
    for a trash the first is None, since the trash gives its own names, and for
    something that was made the second is None."""

    kind: Kind
    pairs: tuple
    # When the action began, by the clock: what a trashed item is found by
    since: float = 0.0

    def describe(self):
        """What is taken back, to follow the word "Undo": "move of 3 items"."""
        count = len(self.pairs)
        now, was = self.pairs[0]
        if self.kind is Kind.CREATE:
            return f"new “{now.get_basename()}”"
        named = now if self.kind is Kind.RENAME else was
        what = f"“{named.get_basename()}”" if count == 1 else f"{count} items"
        if self.kind is Kind.TRASH:
            return f"trash of {what}"
        return f"{self.kind.value} of {what}"


def renamed(pairs):
    """The record of a rename: (where it is now, where it was) of each file. None when
    nothing got another name."""
    changed = tuple((now, was) for now, was in pairs if not locations.same(now, was))
    return Record(Kind.RENAME, changed) if changed else None


def created(location):
    """The record of a new folder or a new file."""
    return Record(Kind.CREATE, ((location, None),))


class History:
    """The actions that can be taken back, the last one first. One for the process:
    jobs belong to it and not to a window."""

    def __init__(self, limit=LIMIT):
        self._limit = limit
        self._records = []

    def push(self, record):
        if record is None or not record.pairs:
            return
        self._records.append(record)
        del self._records[: -self._limit]

    def peek(self):
        return self._records[-1] if self._records else None

    def pop(self):
        return self._records.pop() if self._records else None

    def __len__(self):
        return len(self._records)


# What a job did


def moved(steps):
    """The (where it is now, where it was) of everything a move finished, from its steps.

    A folder that went over whole is one pair; one that only partly did (something in
    it failed, was skipped, or the job was cancelled) gives the pairs of what did go.
    What replaced something is left out, and so is a folder that was merged into one
    that was there: what was in it goes back file by file."""
    children = {}
    for step in steps:
        children.setdefault(id(step.parent), []).append(step)
    pairs = []
    for step in children.get(id(None), []):
        _moved_below(step, children, pairs)
    return tuple(pairs)


def _moved_below(step, children, pairs):
    if step.kind in (StepKind.COPY, StepKind.RENAME):
        if step.done and not step.overwrite:
            pairs.append((step.dest, step.source))
        return
    if step.kind is not StepKind.MAKE_DIR:
        return
    if _whole(step, children):
        pairs.append((step.dest, step.source))
        return
    for child in children.get(id(step), []):
        _moved_below(child, children, pairs)


def _whole(folder, children):
    """Whether a folder was made by the move and everything in it went with it."""
    if not (folder.done and folder.created):
        return False
    for child in children.get(id(folder), []):
        if not child.done or child.overwrite:
            return False
        if child.kind is StepKind.MAKE_DIR and not _whole(child, children):
            return False
    return True


def trashed(steps):
    """The (None, where it was) of everything a trash job moved to the trash. What was
    deleted instead, where there is no trash, is not among them."""
    return tuple((None, step.source) for step in steps if step.kind is StepKind.TRASH and step.done)


# Taking back what finished at once


def rename_back(record):
    """Give each renamed file its old name. Returns where those that got it are now,
    and an OperationError for each that did not.

    Nothing is replaced: a name that something else has now is an error for that file,
    and so is a file that is no longer where the rename left it."""
    renames, errors = [], []
    for now, was in record.pairs:
        try:
            there = locations.file_type(now)
        except GLib.Error as error:
            errors.append(OperationError.from_glib(error, now, "rename"))
            continue
        if there is None:
            errors.append(OperationError.at(now, "rename", "is no longer there"))
        elif not locations.same(now.get_parent(), was.get_parent()):
            errors.append(OperationError.at(now, "rename", "was not renamed in its folder"))
        else:
            renames.append((now, was.get_basename()))
    checked = rename_many.check(renames)
    for rename in checked:
        if rename.problem:
            errors.append(OperationError.at(rename.location, "rename", rename.problem))
    possible = [rename for rename in checked if not rename.problem]
    try:
        back = rename_many.rename_all(possible)
    except OperationError as error:
        # All of these or none: `rename_all` has put back what it had done
        return [], [*errors, error]
    return back, errors


def remove_created(record):
    """Remove the folder or the file an action made, if it is still empty. Returns
    what was removed, and an OperationError for each that was not.

    Only what is empty: a folder with anything in it and a file with anything written
    to it stay, so nothing a user has since put there is ever deleted."""
    removed, errors = [], []
    for made, _was in record.pairs:
        try:
            info = made.query_info("standard::type,standard::size", _NOFOLLOW, None)
            kind = info.get_file_type()
            if kind == Gio.FileType.REGULAR and info.get_size() > 0:
                raise OperationError.at(made, "remove", "is not empty any more")
            if kind not in (Gio.FileType.REGULAR, Gio.FileType.DIRECTORY):
                raise OperationError.at(made, "remove", "is not what was made")
            # Never recursive: a folder that holds anything is refused by the file system
            made.delete(None)
        except OperationError as error:
            errors.append(error)
        except GLib.Error as error:
            if has_code(error, Gio.IOErrorEnum.NOT_FOUND):
                error = OperationError.at(made, "remove", "is no longer there")
            elif has_code(error, Gio.IOErrorEnum.NOT_EMPTY):
                error = OperationError.at(made, "remove", "is not empty any more")
            else:
                error = OperationError.from_glib(error, made, "remove")
            errors.append(error)
        else:
            removed.append(made)
    return removed, errors
