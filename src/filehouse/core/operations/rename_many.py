"""Renaming several files as one operation: the new names, what stands in their way,
and the renaming itself.

Immediate, like the single rename, and with the same error type. Nothing is ever
replaced: a name that is taken, by a file that is there or by another of the new
names, is found before anything is renamed, and blocks all of it.
"""

import re
from dataclasses import dataclass

from gi.repository import Gio, GLib

from filehouse.core import locations
from filehouse.core.errors import OperationError, readable
from filehouse.core.operations import quick
from filehouse.core.operations.conflicts import split_extension

_NUMBER = re.compile("#+")


@dataclass(frozen=True)
class Replace:
    """Every `find` in the name becomes `replacement`."""

    find: str
    replacement: str = ""

    def apply(self, stem, _position):
        return stem.replace(self.find, self.replacement) if self.find else stem


@dataclass(frozen=True)
class Number:
    """The name becomes `pattern` with its "#" as a number, counted from `start` in the
    order of the list: "Holiday #" gives "Holiday 1", "Holiday 2". Several "#" pad the
    number with zeros ("##" gives "01"), and a pattern without one gets it at the end."""

    pattern: str
    start: int = 1

    def apply(self, _stem, position):
        number = self.start + position
        if not _NUMBER.search(self.pattern):
            return f"{self.pattern} {number}".strip()
        return _NUMBER.sub(lambda found: str(number).zfill(len(found.group())), self.pattern)


@dataclass(frozen=True)
class Case:
    upper: bool

    def apply(self, stem, _position):
        return stem.upper() if self.upper else stem.lower()


def new_names(items, rule, with_extension=False):
    """The names `rule` gives to `items`, which are (name, is_dir) in the order of the
    list. The extension of a file stays as it is unless `with_extension`."""
    names = []
    for position, (name, is_dir) in enumerate(items):
        whole = is_dir or with_extension
        stem, extension = (name, "") if whole else split_extension(name)
        names.append(rule.apply(stem, position) + extension)
    return names


@dataclass(frozen=True)
class Rename:
    location: Gio.File
    name: str  # the new one
    problem: str = ""  # why it cannot be done

    @property
    def changes(self):
        return self.name != self.location.get_basename()


def check(pairs):
    """A `Rename` for each (location, new name), with what stands in its way: a name
    that cannot be used, that two of them would get, or that something else has. A
    name another of them has now is free when that one gets a new name first; two that
    trade names cannot be done in any order. Reads and changes nothing."""
    rows = []
    for location, name in pairs:
        problem, target = "", None
        if name != location.get_basename():
            try:
                name = quick.checked_name(name, "rename")
            except OperationError as error:
                problem = error.message
            else:
                target = location.get_parent().get_child(name)
        rows.append([location, name, problem, target])

    # A name another of them has now is free only when that one is given a new name
    wanted = [target.get_uri() for _location, _name, _problem, target in rows if target]
    leaving = {
        location.get_uri()
        for location, _name, problem, target in rows
        if target is not None and not problem
    }
    for row in rows:
        target = row[3]
        if target is None:
            continue
        uri = target.get_uri()
        if wanted.count(uri) > 1:
            row[2] = "two of these would get this name"
        elif uri not in leaving:
            try:
                if locations.file_type(target) is not None:
                    row[2] = "already exists"
            except GLib.Error as error:
                row[2] = readable(error)

    renames = [Rename(location, name, problem) for location, name, problem, _target in rows]
    if not any(rename.problem for rename in renames):
        _ordered, stuck = _order(renames)
        renames = [
            Rename(rename.location, rename.name, "trades names with another of these")
            if rename in stuck
            else rename
            for rename in renames
        ]
    return renames


def rename_all(renames):
    """Give every file its new name and return where each is now, in the same order.

    Nothing is replaced. When one cannot be renamed, those done before it get their
    names back and an OperationError names the file: all of it is done, or none."""
    if any(rename.problem for rename in renames):
        blocked = next(rename for rename in renames if rename.problem)
        raise OperationError.at(blocked.location, "rename", blocked.problem)
    ordered, stuck = _order(renames)
    if stuck:
        raise OperationError.at(stuck[0].location, "rename", "trades names with another of these")
    now = {}
    done = []
    try:
        for rename in ordered:
            renamed = quick.rename(rename.location, rename.name)
            done.append((rename, renamed))
            now[rename.location.get_uri()] = renamed
    except OperationError as error:
        left = 0
        for rename, renamed in reversed(done):
            try:
                quick.rename(renamed, rename.location.get_basename())
            except OperationError:
                left += 1
        if left:
            raise OperationError(
                error.path,
                error.step,
                f"{error.message}; {left} renamed before it could not get the old name back",
            ) from error
        raise
    return [now.get(rename.location.get_uri(), rename.location) for rename in renames]


def _order(renames):
    """The renames that change something, each after the one that frees its name, and
    those that can never be done: every one of them waits for another."""
    pending = [rename for rename in renames if rename.changes]
    ordered = []
    while pending:
        held = {rename.location.get_uri() for rename in pending}
        ready = [rename for rename in pending if _target(rename).get_uri() not in held]
        if not ready:
            break
        ordered += ready
        pending = [rename for rename in pending if rename not in ready]
    return ordered, pending


def _target(rename):
    return rename.location.get_parent().get_child(rename.name)
