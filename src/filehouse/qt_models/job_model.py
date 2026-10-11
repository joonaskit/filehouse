"""Every file operation of this process, as seen from the UI thread.

Jobs run on worker threads. `JobModel` starts them, turns what they report
into queued signals, and is the single source of truth for job state: the
operations panel and the island reporter both observe it.
"""

import time

from PySide6.QtCore import QObject, QTimer, Signal

from filehouse.core import locations
from filehouse.core.operations import Job, JobKind, JobQueue, JobState
from filehouse.qt_models import workers

# At most this often per job, apart from state changes
_UPDATE_SECONDS = 0.1
_KEEP_FINISHED_MS = 4000

ICONS = {
    JobKind.COPY: "edit-copy",
    JobKind.MOVE: "transform-move",
    JobKind.TRASH: "user-trash",
    JobKind.DELETE: "edit-delete",
}


class JobModel(QObject):
    job_added = Signal(object)
    job_changed = Signal(object)
    job_removed = Signal(object)
    # A job waits for `question.answer(...)`. Exactly one listener should give it
    question_asked = Signal(object, object)
    idle = Signal()  # the last active job has ended

    # From worker threads to the UI thread
    _reported = Signal(object)
    _asked = Signal(object, object)

    def __init__(self, parent=None, keep_finished_ms=_KEEP_FINISHED_MS):
        super().__init__(parent)
        self._jobs = []
        self._origins = {}
        self._ended = set()
        self._last_report = {}  # job id -> (state, time); touched only by that job's thread
        self._keep_finished_ms = keep_finished_ms
        self._queue = JobQueue(workers.spawn)
        self._reported.connect(self._on_reported)
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

    def start(self, kind, sources, destination=None, origin=None):
        """Queue a new job and return it."""
        job = Job(kind, sources, destination, prompt=self._prompt, on_change=self._report)
        self._add(job, origin)
        return job

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
        origin = self.origin(job)
        self.dismiss(job)
        self._add(again, origin)
        return again

    def dismiss(self, job):
        """Forget a job that has ended."""
        if job not in self._jobs or not job.state.is_final:
            return
        self._jobs.remove(job)
        self._origins.pop(job.id, None)
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
            return
        self._last_report[job.id] = (state, now)
        try:
            self._reported.emit(job)
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
        self.job_changed.emit(job)
        if not job.state.is_final or job.id in self._ended:
            return
        self._ended.add(job.id)
        self._last_report.pop(job.id, None)
        if job.state is not JobState.FAILED:
            # A failed job stays until it is dismissed
            QTimer.singleShot(self._keep_finished_ms, self, lambda: self.dismiss(job))
        if not self.active_count():
            self.idle.emit()


def title(job):
    """One line saying what the job is, or how it ended."""
    if job.state.is_final:
        return job.summary
    count = len(job.sources)
    what = f"“{locations.basename(job.sources[0])}”" if count == 1 else f"{count} items"
    if job.kind is JobKind.TRASH:
        return f"Moving {what} to the trash"
    if job.kind is JobKind.DELETE:
        return f"Deleting {what}"
    verb = "Copying" if job.kind is JobKind.COPY else "Moving"
    return f"{verb} {what} to {locations.display_name(job.destination)}"


def text(job):
    """The line under the title: what is happening now, or what went wrong."""
    state = job.state
    if state is JobState.QUEUED:
        return "Waiting to start"
    if state is JobState.PLANNING:
        return "Preparing…"
    if state is JobState.WAITING:
        return "Waiting for your answer"
    if state is JobState.RUNNING:
        progress = job.progress
        if progress.files_total > 1:
            position = min(progress.files_done + 1, progress.files_total)
            return f"{progress.current} ({position} of {progress.files_total})"
        return progress.current
    if state is JobState.FAILED and job.errors:
        more = len(job.errors) - 1
        return str(job.error) + (f", and {more} more" if more else "")
    return ""
