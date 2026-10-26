"""Folder automations (#87): rules that act on the files in a folder.

A rule is data: conditions, and actions that run in order on the same file. This
module holds the rules, checks that one can work, finds what a set of rules would do
to a folder now (`plan_run`), and reads and writes them as JSON. Doing it is the
operations engine's: nothing here changes a file.
"""

import fnmatch
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from gi.repository import Gio, GLib

from filehouse.core import locations
from filehouse.core.operations.conflicts import split_extension

# A file changed more recently than this is left for the next run: it may still be written
SETTLE_SECONDS = 10
# What browsers and this app name a file until it is whole
TEMPORARY_SUFFIXES = (".part", ".crdownload", ".download", ".tmp", ".filepart")
TEMPORARY_PREFIX = ".filehouse-"
DAY_SECONDS = 24 * 60 * 60

_ATTRIBUTES = ",".join(
    (
        Gio.FILE_ATTRIBUTE_STANDARD_NAME,
        Gio.FILE_ATTRIBUTE_STANDARD_TYPE,
        Gio.FILE_ATTRIBUTE_STANDARD_IS_SYMLINK,
        Gio.FILE_ATTRIBUTE_STANDARD_IS_HIDDEN,
        Gio.FILE_ATTRIBUTE_STANDARD_CONTENT_TYPE,
        Gio.FILE_ATTRIBUTE_STANDARD_SIZE,
        Gio.FILE_ATTRIBUTE_TIME_MODIFIED,
    )
)

PLACEHOLDERS = ("name", "ext", "date")
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")


class Match(Enum):
    ALL = "all"
    ANY = "any"


class ConditionKind(Enum):
    NAME = "name"  # mode: glob, contains or regex
    TYPE = "type"  # an extension (".pdf") or a content type ("image/*"), several with ","
    SIZE = "size"  # mode: larger or smaller; value in bytes
    AGE = "age"  # mode: older or newer; value in days since it was modified


NAME_MODES = ("glob", "contains", "regex")
SIZE_MODES = ("larger", "smaller")
AGE_MODES = ("older", "newer")
_MODES = {
    ConditionKind.NAME: NAME_MODES,
    ConditionKind.TYPE: ("is",),
    ConditionKind.SIZE: SIZE_MODES,
    ConditionKind.AGE: AGE_MODES,
}


class ActionKind(Enum):
    RENAME = "rename"  # value: a template with {name}, {ext} and {date}
    MOVE = "move"  # value: a folder
    COPY = "copy"  # value: a folder
    TRASH = "trash"

    @property
    def leaves(self):
        """Whether the file is not in the folder after this."""
        return self in (ActionKind.MOVE, ActionKind.TRASH)

    @property
    def needs_value(self):
        return self is not ActionKind.TRASH


@dataclass(frozen=True)
class Condition:
    kind: ConditionKind
    mode: str
    value: object


@dataclass(frozen=True)
class Action:
    kind: ActionKind
    value: str = ""


@dataclass(frozen=True)
class Rule:
    name: str
    conditions: tuple
    actions: tuple
    match: Match = Match.ALL
    enabled: bool = True
    # No more rules for a file this one matched
    stop: bool = False


@dataclass(frozen=True)
class Facts:
    """What the conditions look at in a file. The name is the one the file has when a
    rule looks at it: an earlier rule may have renamed it."""

    name: str
    size: int
    modified: float
    content_type: str


# Checking a rule


def check_rule(rule, folder=None):
    """What stops `rule` from working, as texts to show beside the fields; empty when
    it can. With the `folder` it is for, a destination that is that folder or that
    cannot be written to is also found."""
    problems = []
    if not rule.name.strip():
        problems.append("The rule needs a name")
    if not rule.conditions:
        problems.append("Add a condition: a rule with none would act on every file")
    for condition in rule.conditions:
        problems += _check_condition(condition)
    if not rule.actions:
        problems.append("Add an action")
    for index, action in enumerate(rule.actions):
        problems += _check_action(action, folder)
        if action.kind.leaves and index < len(rule.actions) - 1:
            problems.append(
                f"“{action.kind.value.capitalize()}” takes the file out of the folder: "
                "nothing can follow it"
            )
    return problems


def _check_condition(condition):
    if condition.mode not in _MODES[condition.kind]:
        return [f"“{condition.mode}” is not a way to compare {condition.kind.value}"]
    if condition.kind is ConditionKind.NAME:
        if not str(condition.value):
            return ["A name condition needs something to look for"]
        if condition.mode == "regex":
            try:
                re.compile(str(condition.value))
            except re.error as error:
                return [f"The pattern is not a regular expression: {error}"]
    elif condition.kind is ConditionKind.TYPE:
        if not _types(condition.value):
            return ["A type condition needs an extension or a content type"]
    else:
        try:
            if float(condition.value) < 0:
                raise ValueError
        except (TypeError, ValueError):
            return [f"The {condition.kind.value} must be a number, zero or more"]
    return []


def _check_action(action, folder):
    if not action.kind.needs_value:
        return []
    value = str(action.value).strip()
    verb = action.kind.value.capitalize()
    if not value:
        return [f"“{verb}” needs a {'name' if action.kind is ActionKind.RENAME else 'folder'}"]
    if action.kind is ActionKind.RENAME:
        return _check_template(value)
    destination = destination_folder(value)
    if destination is None:
        return [f"“{value}” is not a folder on this computer"]
    if folder is not None and locations.same(destination, folder):
        return [f"“{verb}” to the folder the rule is for does nothing"]
    if not _can_write_to(destination):
        return [f"Nothing can be put in “{value}”"]
    return []


def _check_template(template):
    unknown = [name for name in _PLACEHOLDER.findall(template) if name not in PLACEHOLDERS]
    if unknown:
        offered = ", ".join("{" + name + "}" for name in PLACEHOLDERS)
        return [f"“{{{unknown[0]}}}” is not known: use {offered}"]
    if _PLACEHOLDER.sub("", template).count("{") or _PLACEHOLDER.sub("", template).count("}"):
        return ["A { or } in the name has no match"]
    if "/" in template or not template.strip():
        return ["A name cannot be empty or hold a /"]
    return []


def _can_write_to(destination):
    """Whether files can be put in `destination`, or in the nearest folder above it that
    exists, which is where it would be made."""
    probe = destination
    while probe is not None:
        file_type = _type_at(probe)
        if file_type is not None:
            return file_type == Gio.FileType.DIRECTORY and _writable(probe)
        probe = probe.get_parent()
    return False


def _type_at(location):
    """What is at `location`, or None when nothing is or it cannot be told."""
    try:
        return locations.file_type(location)
    except GLib.Error:
        return None


def _writable(folder):
    try:
        info = folder.query_info(
            Gio.FILE_ATTRIBUTE_ACCESS_CAN_WRITE, Gio.FileQueryInfoFlags.NONE, None
        )
        return info.get_attribute_boolean(Gio.FILE_ATTRIBUTE_ACCESS_CAN_WRITE)
    except GLib.Error:
        return False


def destination_folder(text):
    """The folder a destination means: a path, `~` or a file:// URI, local only. None
    for anything else."""
    text = str(text).strip()
    # A relative path would mean the folder the process was started in
    if not text.startswith(("/", "~", "file://")):
        return None
    location = locations.from_user_input(text)
    if location is None or not location.is_native():
        return None
    return location


# Matching


def matches(rule, facts, now):
    """Whether `rule` applies to a file that has these facts."""
    results = (_holds(condition, facts, now) for condition in rule.conditions)
    return any(results) if rule.match is Match.ANY else all(results)


def _holds(condition, facts, now):
    mode, value = condition.mode, condition.value
    if condition.kind is ConditionKind.NAME:
        return _name_holds(mode, str(value), facts.name)
    if condition.kind is ConditionKind.TYPE:
        return _type_holds(_types(value), facts)
    if condition.kind is ConditionKind.SIZE:
        size = float(value)
        return facts.size > size if mode == "larger" else facts.size < size
    age = (now - facts.modified) / DAY_SECONDS
    return age > float(value) if mode == "older" else age < float(value)


def _name_holds(mode, pattern, name):
    if mode == "glob":
        return fnmatch.fnmatchcase(name.lower(), pattern.lower())
    if mode == "contains":
        return pattern.lower() in name.lower()
    try:
        return re.search(pattern, name, re.IGNORECASE) is not None
    except re.error:
        return False


def _types(value):
    return [part.strip().lower() for part in str(value).split(",") if part.strip()]


def _type_holds(types, facts):
    _stem, extension = split_extension(facts.name)
    content_type = facts.content_type.lower()
    for wanted in types:
        if wanted.startswith("."):
            if extension.lower().endswith(wanted):
                return True
        elif fnmatch.fnmatchcase(content_type, wanted):
            return True
    return False


# Names


def render_name(template, facts):
    """The name a rename template gives a file. {name} is the name without its
    extension, {ext} the extension without its dot, {date} the day and time it was last
    changed ("2026-10-25 14.03": a colon is no good in a name)."""
    stem, extension = split_extension(facts.name)
    values = {
        "name": stem,
        "ext": extension.lstrip("."),
        "date": datetime.fromtimestamp(facts.modified).strftime("%Y-%m-%d %H.%M"),
    }
    return _PLACEHOLDER.sub(lambda found: values.get(found.group(1), found.group(0)), template)


def is_temporary(name):
    return name.startswith(TEMPORARY_PREFIX) or name.lower().endswith(TEMPORARY_SUFFIXES)


# What a run would do


@dataclass(frozen=True)
class Step:
    """One thing a rule does to a file: `kind`, and the name the file has when it
    happens. For a move or copy `folder` is where it goes, and `new_folder` says it
    does not exist yet and is made."""

    kind: ActionKind
    name: str
    rule: str
    folder: object = None
    new_folder: bool = False
    # For a rename: what it is renamed to
    target: str = ""


@dataclass
class PlannedFile:
    source: object
    steps: list = field(default_factory=list)


@dataclass
class Plan:
    """What a run would do. `files` are the files some rule acts on, `still_changing`
    the matching candidates left for the next run, `problems` the rules that could not
    be used (a rule that cannot work is skipped whole, not run in part)."""

    files: list = field(default_factory=list)
    still_changing: list = field(default_factory=list)
    problems: dict = field(default_factory=dict)

    @property
    def step_count(self):
        return sum(len(planned.steps) for planned in self.files)


def candidates(folder, cancellable=None):
    """The files a rule can act on: regular, visible files directly in `folder`, as
    (Gio.File, Facts). Folders, links and hidden files are left alone. A folder that
    cannot be read raises the GLib.Error."""
    found = []
    enumerator = folder.enumerate_children(
        _ATTRIBUTES, Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS, cancellable
    )
    try:
        for info in enumerator:
            if info.get_file_type() != Gio.FileType.REGULAR or info.get_is_symlink():
                continue
            if info.get_is_hidden():
                continue
            facts = Facts(
                name=info.get_name(),
                size=info.get_size(),
                modified=info.get_modification_date_time().to_unix()
                if info.get_modification_date_time()
                else 0.0,
                content_type=info.get_content_type() or "",
            )
            found.append((folder.get_child(facts.name), facts))
    finally:
        enumerator.close(None)
    found.sort(key=lambda pair: pair[1].name.lower())
    return found


def plan_run(folder, rules, now=None, cancellable=None, names=None):
    """What `rules` would do to the files in `folder` now, each file taken once.

    Rules go from the first, each on the name and place the file has by then. A move or
    a trash ends it, since the file has left; so does a rule that stops. A file changed
    in the last `SETTLE_SECONDS`, or named as one still being written, is left for the
    next run.

    With `names`, the run is for files that arrived (#88): only those are looked at, and
    a rule with an age condition is left out, since a copy keeps the age of its source."""
    now = time.time() if now is None else now
    plan = Plan()
    usable = []
    for rule in rules:
        if not rule.enabled:
            continue
        if names is not None and has_age(rule):
            continue
        problems = check_rule(rule, folder)
        if problems:
            plan.problems[rule.name] = problems
        else:
            usable.append(rule)
    if not usable:
        return plan
    for source, facts in candidates(folder, cancellable):
        if names is not None and facts.name not in names:
            continue
        steps, current = [], facts
        for rule in usable:
            if not matches(rule, current, now):
                continue
            ended, current = _apply(rule, current, steps)
            if ended or rule.stop:
                break
        if not steps:
            continue
        if is_temporary(facts.name) or now - facts.modified < SETTLE_SECONDS:
            plan.still_changing.append(source)
            continue
        plan.files.append(PlannedFile(source, steps))
    return plan


def has_age(rule):
    """Whether `rule` looks at how old a file is: such a rule does not act on arrival."""
    return any(condition.kind is ConditionKind.AGE for condition in rule.conditions)


def _apply(rule, facts, steps):
    """Add the steps of `rule` for a file; returns (whether the file left the folder, the
    facts the next rule sees)."""
    for action in rule.actions:
        if action.kind is ActionKind.RENAME:
            target = render_name(action.value.strip(), facts)
            if target == facts.name:
                continue
            steps.append(Step(action.kind, facts.name, rule.name, target=target))
            facts = Facts(target, facts.size, facts.modified, facts.content_type)
        elif action.kind is ActionKind.TRASH:
            steps.append(Step(action.kind, facts.name, rule.name))
            return True, facts
        else:
            destination = destination_folder(action.value)
            new = _type_at(destination) is None
            steps.append(Step(action.kind, facts.name, rule.name, destination, new))
            if action.kind.leaves:
                return True, facts
    return False, facts


# Storing


def rule_to_json(rule):
    return {
        "name": rule.name,
        "enabled": rule.enabled,
        "match": rule.match.value,
        "stop": rule.stop,
        "conditions": [
            {"kind": c.kind.value, "mode": c.mode, "value": c.value} for c in rule.conditions
        ],
        "actions": [{"kind": a.kind.value, "value": a.value} for a in rule.actions],
    }


def rule_from_json(data):
    """A rule from what was stored, or None when it cannot be one: a file that was
    edited by hand is not trusted, and a rule is dropped whole rather than read in part."""
    try:
        conditions = tuple(
            Condition(ConditionKind(c["kind"]), str(c["mode"]), c["value"])
            for c in data["conditions"]
        )
        actions = tuple(
            Action(ActionKind(a["kind"]), str(a.get("value", ""))) for a in data["actions"]
        )
        return Rule(
            name=str(data["name"]),
            conditions=conditions,
            actions=actions,
            match=Match(data.get("match", "all")),
            enabled=bool(data.get("enabled", True)),
            stop=bool(data.get("stop", False)),
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def rules_to_json(rules):
    return [rule_to_json(rule) for rule in rules]


def rules_from_json(items):
    if not isinstance(items, list):
        return []
    return [rule for rule in map(rule_from_json, items) if rule is not None]
