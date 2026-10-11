"""What a job asks the user, the answers it accepts, and names for "Keep both".

A job is given one prompt callback. It is called on the job's thread with a
question and either returns the answer, or returns None and gives the answer
later, from any thread, with `question.answer(...)`. The job does the waiting
and stops waiting when it is cancelled, so an unanswered question can never
keep a worker thread.
"""

import threading
from dataclasses import dataclass
from enum import Enum

from filehouse.core import locations

# How often a waiting job looks at its cancellable
_POLL_SECONDS = 0.05


class Answer(Enum):
    REPLACE = "replace"  # a folder onto a folder merges
    SKIP = "skip"
    KEEP_BOTH = "keep-both"
    RETRY = "retry"
    DELETE = "delete"  # permanently
    CANCEL = "cancel"  # the whole job


@dataclass(frozen=True)
class Reply:
    answer: Answer
    apply_to_all: bool = False


class Exists(Exception):
    """Something is already at a step's destination. `file_type` says what."""

    def __init__(self, file_type):
        super().__init__("destination exists")
        self.file_type = file_type


class Question:
    answers = ()

    def __init__(self):
        self._answered = threading.Event()
        self._reply = None

    def answer(self, reply):
        """Give the answer from any thread: an `Answer` or a `Reply`."""
        self._reply = reply
        self._answered.set()

    def wait(self, cancellable):
        """Block until answered. Returns None when the job is cancelled first."""
        while not self._answered.wait(_POLL_SECONDS):
            if cancellable.is_cancelled():
                return None
        return self._reply


class Conflict(Question):
    """The destination of `source` exists. Replace is not offered when the two are of
    different kinds (a file and a folder) or are the same file."""

    def __init__(self, source, destination, source_is_dir, destination_is_dir, can_replace):
        super().__init__()
        self.source = source
        self.destination = destination
        self.source_is_dir = source_is_dir
        self.destination_is_dir = destination_is_dir
        self.can_replace = can_replace

    @property
    def answers(self):
        replace = (Answer.REPLACE,) if self.can_replace else ()
        return (*replace, Answer.SKIP, Answer.KEEP_BOTH, Answer.CANCEL)


class Failure(Question):
    """A step failed with `error`. Skip with apply-to-all is "Skip all"."""

    answers = (Answer.SKIP, Answer.RETRY, Answer.CANCEL)

    def __init__(self, error):
        super().__init__()
        self.error = error


class TrashUnsupported(Question):
    """`location` can't go to the trash where it is. Delete it permanently instead?"""

    answers = (Answer.DELETE, Answer.SKIP, Answer.CANCEL)

    def __init__(self, location):
        super().__init__()
        self.location = location


class ConfirmDelete(Question):
    """Asked before a permanent delete touches anything."""

    answers = (Answer.DELETE, Answer.CANCEL)

    def __init__(self, sources):
        super().__init__()
        self.sources = tuple(sources)


def as_reply(value):
    return value if isinstance(value, Reply) else Reply(Answer(value))


def split_extension(name):
    """("report", ".pdf"), ("archive", ".tar.gz"), (".bashrc", "")."""
    stem, dot, extension = name.rpartition(".")
    if not dot or not stem:
        return name, ""
    if stem.endswith(".tar") and len(stem) > 4:
        return stem[:-4], ".tar." + extension
    return stem, "." + extension


def free_name(folder, name, is_dir, cancellable=None):
    """`name` with " (2)", " (3)", … before the extension: the first not taken in `folder`."""
    stem, extension = (name, "") if is_dir else split_extension(name)
    number = 2
    while True:
        candidate = f"{stem} ({number}){extension}"
        if locations.file_type(folder.get_child(candidate), cancellable) is None:
            return candidate
        number += 1
