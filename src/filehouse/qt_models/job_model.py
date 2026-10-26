"""Every file operation of this process, as seen from the UI thread.

Jobs run on worker threads. `JobModel` starts them, turns what they report
into queued signals, and is the single source of truth for job state: the
operations panel and the island reporter both observe it.
"""

import time
from collections import deque
from dataclasses import dataclass

from PySide6.QtCore import QLocale, QObject, QTimer, Signal

from filehouse.core import locations
from filehouse.core.operations import Job, JobKind, JobQueue, JobState, undo
from filehouse.core.operations.archive import CompressJob, ExtractJob
from filehouse.core.operations.automation import RulesJob, RulesUndoJob
from filehouse.core.operations.permissions import PermissionsJob
from filehouse.qt_models import workers
from filehouse.qt_models.workers import Background

# At most this often per job, apart from state changes
_UPDATE_SECONDS = 0.1
_KEEP_FINISHED_MS = 4000
# Speed is measured over the last few seconds, and not from less than this
_SPEED_WINDOW_SECONDS = 3.0
_SPEED_MIN_SECONDS = 0.5

ICONS = {
    JobKind.COPY: "edit-copy",
    JobKind.DUPLICATE: "edit-duplicate",
    JobKind.MOVE: "transform-move",
    JobKind.TRASH: "user-trash",
    JobKind.RESTORE: "edit-undo",
    JobKind.DELETE: "edit-delete",
    JobKind.EXTRACT: "archive-extract",
    JobKind.COMPRESS: "archive-insert",
    JobKind.PERMISSIONS: "object-locked",
    JobKind.RULES: "view-filter",
}


@dataclass(frozen=True)
class Undone:
    """What taking an action back came to. A move or a trash is taken back by `job`,
    which reports as any job does; a rename or something that was made is taken back
    at once, with where the files are now in `changed` and what could not be done, by
    name, in `errors`."""

    record: undo.Record
    job: Job | None = None
    changed: tuple = ()
    errors: tuple = ()


class JobModel(QObject):
    job_added = Signal(object)
    job_changed = Signal(object)
    job_removed = Signal(object)
    # A job waits for `question.answer(...)`. Exactly one listener should give it
    question_asked = Signal(object, object)
    idle = Signal()  # the last active job has ended
    # What Undo would take back is another action now, or none
    undo_changed = Signal()

    # From worker threads to the UI thread
    _reported = Signal(object)
    _held = Signal(object)  # a report was held back: it is sent once the interval is over
    _asked = Signal(object, object)

    def __init__(self, parent=None, keep_finished_ms=_KEEP_FINISHED_MS):
        super().__init__(parent)
        self._jobs = []
        self._origins = {}
        self._predecessors = {}
        self._samples = {}  # job id -> recent (time, bytes done), for the speed
        self._ended = set()
        self._last_report = {}  # job id -> (state, time); touched only by that job's thread
        self._held_back = set()  # ids of the jobs with a report waiting to be sent
        self._keep_finished_ms = keep_finished_ms
        self._queue = JobQueue(workers.spawn)
        # The actions that can be taken back, of every window: jobs belong to the process
        self._history = undo.History()
        # Taking back a rename or a new folder: on a worker, after what was asked before
        self._background = Background(self)
        self._reported.connect(self._on_reported)
        self._held.connect(self._on_held)
        self._asked.connect(self.question_asked)
        # Nobody can answer a job once its model is gone, so it must not wait
        jobs = self._jobs
        self.destroyed.connect(lambda: [job.cancel() for job in jobs])

    @property
    def jobs(self):
        return list(self._jobs)

    def active_count(self):
        return sum(not job.state.is_final for job in self._jobs)

    def origin(self, job):
        """Whatever was passed as `origin` when the job was started."""
        return self._origins.get(job.id)

    def predecessor(self, job):
        """The failed job this one is a retry of, or None."""
        return self._predecessors.get(job.id)

    def speed(self, job):
        """Bytes per second over the last few seconds, or None when it can't be told."""
        samples = self._samples.get(job.id)
        if job.state is not JobState.RUNNING or not samples:
            return None
        (first_time, first_bytes), (last_time, last_bytes) = samples[0], samples[-1]
        if last_time - first_time < _SPEED_MIN_SECONDS or last_bytes <= first_bytes:
            return None
        return (last_bytes - first_bytes) / (last_time - first_time)

    def start(self, kind, sources, destination=None, origin=None, targets=None):
        """Queue a new job and return it. `targets` is for a restore: see `Job`."""
        job = Job(
            kind,
            sources,
            destination,
            targets=targets,
            prompt=self._prompt,
            on_change=self._report,
        )
        self._add(job, origin)
        return job

    def extract(self, archive, destination, origin=None):
        """Queue the extraction of an archive into a folder, and return the job."""
        job = ExtractJob(archive, destination, prompt=self._prompt, on_change=self._report)
        self._add(job, origin)
        return job

    def compress(self, sources, destination, name, format, origin=None):
        """Queue the making of the archive `name` in a folder, and return the job."""
        job = CompressJob(
            sources, destination, name, format, prompt=self._prompt, on_change=self._report
        )
        self._add(job, origin)
        return job

    def set_permissions(self, preset, sources, origin=None):
        """Queue giving `sources` the permissions of `preset`, and return the job."""
        job = PermissionsJob(preset, sources, prompt=self._prompt, on_change=self._report)
        self._add(job, origin)
        return job

    def run_rules(self, folder, plan, origin=None):
        """Queue doing the `plan` of a folder's rules (`automations.plan_run`), and
        return the job. Nobody is asked anything while it runs."""
        job = RulesJob(folder, plan, on_change=self._report)
        self._add(job, origin)
        return job

    def remember(self, record):
        """Keep an action so that it can be taken back. A job's own is kept when it
        ends; this is for what the windows do at once: a rename, a new folder."""
        if record is None or record.empty:
            return
        self._history.push(record)
        self.undo_changed.emit()

    def undo_text(self):
        """What Undo would take back, to follow the word: "move of 3 items". None when
        there is nothing."""
        record = self._history.peek()
        return record.describe() if record is not None else None

    @property
    def busy(self):
        """Whether a rename or a new folder is being taken back."""
        return self._background.busy

    def undo(self, origin=None, done=None):
        """Take back the last action that can be. What is taken back is not kept to be
        done again: there is no redo.

        A move or a trash is taken back by a job: the `Undone` with it is returned. A
        rename or something that was made is taken back on a worker, in turn after what
        was asked for before it (the rename itself, say), and `done(Undone)` is called
        once it is. Then, and when there is nothing to take back, None is returned."""
        record = self._history.pop()
        if record is None:
            return None
        self.undo_changed.emit()
        if record.kind in (undo.Kind.MOVE, undo.Kind.TRASH, undo.Kind.RULES):
            if record.kind is undo.Kind.RULES:
                job = RulesUndoJob(record, on_change=self._report)
            else:
                job = Job.undo_of(record, prompt=self._prompt, on_change=self._report)
            self._add(job, origin)
            return Undone(record, job=job)

        def take_back():
            if record.kind is undo.Kind.RENAME:
                return undo.rename_back(record)
            return undo.remove_created(record)

        def finished(result):
            changed, errors = result
            if done is not None:
                done(Undone(record, changed=tuple(changed), errors=tuple(errors)))

        self._background.call(take_back, finished)
        return None

    def cancel(self, job):
        job.cancel()

    def cancel_all(self):
        for job in self._jobs:
            if not job.state.is_final:
                job.cancel()

    def retry(self, job):
        """Run the failed items of `job` again, as a new job in its place."""
        again = job.retry()
        if again is None:
            return None
        # Added before the old one goes, so observers can hand over from one to the other
        self._predecessors[again.id] = job
        self._add(again, self.origin(job))
        self.dismiss(job)
        return again

    def dismiss(self, job):
        """Forget a job that has ended."""
        if job not in self._jobs or not job.state.is_final:
            return
        self._jobs.remove(job)
        self._origins.pop(job.id, None)
        self._predecessors.pop(job.id, None)
        self._samples.pop(job.id, None)
        self._ended.discard(job.id)
        self.job_removed.emit(job)

    def _add(self, job, origin):
        self._jobs.append(job)
        self._origins[job.id] = origin
        self.job_added.emit(job)
        self._queue.add(job)

    # Called on worker threads

    def _report(self, job):
        state, now = job.state, time.monotonic()
        last_state, last_time = self._last_report.get(job.id, (None, 0.0))
        if state is last_state and now - last_time < _UPDATE_SECONDS:
            # Sent when the interval is over, or the last report before a pause, a slow
            # file say, would be the one lost
            if job.id not in self._held_back:
                self._held_back.add(job.id)
                self._send(self._held, job)
            return
        self._last_report[job.id] = (state, now)
        self._send(self._reported, job)

    def _send(self, signal, job):
        try:
            signal.emit(job)
        except RuntimeError:
            # The model is gone; the job still runs to its end
            pass

    def _prompt(self, question):
        # Answered later, from the UI thread: the job does the waiting
        try:
            self._asked.emit(question.job, question)
        except RuntimeError:
            question.job.cancel()
        return None

    # On the UI thread

    def _on_reported(self, job):
        if job not in self._jobs:
            return
        self._sample(job)
        self.job_changed.emit(job)
        if not job.state.is_final or job.id in self._ended:
            return
        self._ended.add(job.id)
        self._last_report.pop(job.id, None)
        # Also of a job that was cancelled or failed: what it did finish
        self.remember(job.undo)
        if job.state is not JobState.FAILED:
            # A failed job stays until it is dismissed
            QTimer.singleShot(self._keep_finished_ms, self, lambda: self.dismiss(job))
        if not self.active_count():
            self.idle.emit()

    def _on_held(self, job):
        QTimer.singleShot(round(_UPDATE_SECONDS * 1000), self, lambda: self._send_held(job))

    def _send_held(self, job):
        # Let go first: a report held back after this is sent at its own time. This one
        # shows the job as it is now, with whatever was held back
        self._held_back.discard(job.id)
        self._on_reported(job)

    def _sample(self, job):
        now = time.monotonic()
        samples = self._samples.setdefault(job.id, deque())
        samples.append((now, job.progress.bytes_done))
        while now - samples[0][0] > _SPEED_WINDOW_SECONDS:
            samples.popleft()


def title(job):
    """One line saying what the job is, or how it ended."""
    return job.summary if job.state.is_final else heading(job)


def short_heading(job):
    """What the job is doing, without where to: "Copying 3 items". For where room is short."""
    if job.kind is JobKind.RULES:
        if job.is_undo:
            return "Taking back folder rules"
        count = len(job.sources)
        return f"Running rules on {'1 file' if count == 1 else f'{count} files'}"
    count = len(job.sources)
    what = f"“{locations.basename(job.sources[0])}”" if count == 1 else f"{count} items"
    if job.kind is JobKind.TRASH:
        return f"Moving {what} to the trash"
    if job.kind is JobKind.RESTORE:
        return f"Restoring {what}"
    if job.kind is JobKind.DELETE:
        return f"Deleting {what}"
    if job.kind is JobKind.EXTRACT:
        return f"Extracting {what}"
    if job.kind is JobKind.COMPRESS:
        return f"Compressing {what}"
    if job.kind is JobKind.DUPLICATE:
        return f"Duplicating {what}"
    if job.kind is JobKind.PERMISSIONS:
        return f"Changing the permissions of {what}"
    return f"{'Copying' if job.kind is JobKind.COPY else 'Moving'} {what}"


def verb(job):
    """The one word for what the job is doing: "Copying"."""
    return short_heading(job).split()[0]


def heading(job):
    """What the job is doing, whatever state it is in: "Copying 3 items to Pictures"."""
    text = short_heading(job)
    if job.kind is JobKind.MOVE and job.destination is None:
        # A move that is taken back: each item to where it was, not all to one folder
        return f"{text} back"
    if job.kind in (JobKind.COPY, JobKind.MOVE, JobKind.EXTRACT):
        text += f" to {locations.display_name(job.destination)}"
    elif job.kind is JobKind.RULES and job.destination is not None:
        text += f" in {locations.display_name(job.destination)}"
    return text


def overall_fraction(jobs):
    """How far several jobs are together, 0.0 to 1.0: by bytes when all of them count
    bytes, otherwise the mean of how far each is. None while none of them knows.
    As with one job, 1.0 does not mean done."""
    known = [job.progress for job in jobs if job.progress.fraction is not None]
    if not known:
        return None
    if len(known) == len(jobs) and all(progress.bytes_total for progress in known):
        total = sum(progress.bytes_total for progress in known)
        return min(1.0, sum(progress.bytes_done for progress in known) / total)
    # A job that is still being planned counts as not started
    return sum(progress.fraction for progress in known) / len(jobs)


def error_text(job):
    """The first error of a failed job, and how many more there are."""
    if not job.errors:
        return ""
    more = len(job.errors) - 1
    return str(job.error) + (f", and {more} more" if more else "")


def text(job, speed=None):
    """The line under the title: what is happening now, or what went wrong.
    `speed` is in bytes per second, from `JobModel.speed`."""
    state = job.state
    if state is JobState.QUEUED:
        return "Waiting to start"
    if state is JobState.PLANNING:
        return "Preparing…"
    if state is JobState.WAITING:
        return "Waiting for your answer"
    if state is JobState.RUNNING:
        progress = job.progress
        line = progress.current
        if progress.files_total > 1:
            position = min(progress.files_done + 1, progress.files_total)
            line += f" ({position} of {progress.files_total})"
        if speed:
            line += f", {QLocale().formattedDataSize(int(speed))}/s"
        return line
    if state is JobState.FAILED:
        return error_text(job)
    return ""
