"""The operations engine: every user action that changes files runs as a Job."""

from filehouse.core.errors import OperationError
from filehouse.core.operations.conflicts import (
    Answer,
    ConfirmDelete,
    Conflict,
    Failure,
    Reply,
    TrashUnsupported,
)
from filehouse.core.operations.job import Job, JobKind, JobProgress, JobState
from filehouse.core.operations.queue import JobQueue

__all__ = [
    "Answer",
    "ConfirmDelete",
    "Conflict",
    "Failure",
    "Job",
    "JobKind",
    "JobProgress",
    "JobQueue",
    "JobState",
    "OperationError",
    "Reply",
    "TrashUnsupported",
]
