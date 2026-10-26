"""Running a folder's rules (#87), and taking a run back.

A run is one job (`RulesJob`), so it is one entry in the panel and the island and one
action for Undo. It does what `automations.plan_run` found, file by file, each step
on the name and place the step before it left the file at. A move, a copy and a trash
are ordinary jobs run inside it, so every safety rule of those holds: nothing is
replaced, a name that is taken gets " (2)", and the trash never falls back to a
permanent delete. Nobody is asked anything: a step that fails is an error naming the
file, and the steps after it are left out for that file.
"""

from dataclasses import replace

from gi.repository import Gio, GLib

from filehouse.core import locations
from filehouse.core.automations import ActionKind
from filehouse.core.errors import OperationError, has_code, is_cancelled
from filehouse.core.operations import quick, undo
from filehouse.core.operations.conflicts import Answer, Reply, free_name
from filehouse.core.operations.job import Job, JobKind, JobState, _Cancelled

_INNER = {
    ActionKind.MOVE: JobKind.MOVE,
    ActionKind.COPY: JobKind.COPY,
    ActionKind.TRASH: JobKind.TRASH,
}


class RulesJob(Job):
    """Do the `plan` of the rules of `folder`. `sources` are the files it acts on."""

    def __init__(self, folder, plan, *, on_change=None):
        sources = [planned.source for planned in plan.files]
        super().__init__(JobKind.RULES, sources, folder, on_change=on_change)
        self.folder = folder
        self.plan = plan
        self.acted = 0  # files every step of which finished
        self._parts = []  # the undo records of what was done, in order
        # Where the run put files, renamed or moved or copied: their arrival there is
        # its own doing, not a file for the rules (#88)
        self.placed = []
        self._complete = False

    @property
    def can_retry(self):
        # The next run of the rules is the retry: it finds what is left
        return False

    def retry(self):
        return None

    def _work(self):
        self._set_progress(files_total=self.plan.step_count, planned=True)
        self._set_state(JobState.RUNNING)
        for planned in self.plan.files:
            self._check_cancelled()
            self._run_file(planned)
        self._complete = True

    def _run_file(self, planned):
        location = planned.source
        for index, step in enumerate(planned.steps):
            self._check_cancelled()
            self._set_progress(current=step.name)
            try:
                location = self._do(step, location)
            except OperationError as error:
                self.errors.append(error)
                # The steps after a failed one are left out for this file
                left = len(planned.steps) - index
                self._set_progress(files_done=self.progress.files_done + left)
                return
            self._set_progress(files_done=self.progress.files_done + 1)
        self.acted += 1

    def _do(self, step, location):
        """Do one step to the file at `location`; returns where the file is after it."""
        if step.kind is ActionKind.RENAME:
            return self._rename(location, step.target)
        folder = step.folder
        if step.kind is not ActionKind.TRASH:
            self._make_folder(folder)
        job = self._inner(_INNER[step.kind], location, folder)
        return location if step.kind is ActionKind.COPY else job

    def _rename(self, location, target):
        name = quick.checked_name(target, "rename")
        folder = location.get_parent()
        while True:
            try:
                renamed = location.set_display_name(name, self.cancellable)
                break
            except GLib.Error as error:
                if is_cancelled(error):
                    raise _Cancelled from None
                if not has_code(error, Gio.IOErrorEnum.EXISTS):
                    raise OperationError.from_glib(error, location, "rename") from error
            # Never replaced: the name gets a number, as "Keep both" gives one
            try:
                name = free_name(folder, target, False, self.cancellable)
            except GLib.Error as error:
                raise OperationError.from_glib(error, location, "rename") from error
        self._parts.append(undo.renamed([(renamed, location)]))
        self.placed.append(renamed)
        return renamed

    def _make_folder(self, folder):
        """Make `folder` and the folders above it that are missing, each kept to be
        taken back (if still empty) after what was put in it."""
        missing = []
        probe = folder
        try:
            while probe is not None and locations.file_type(probe, self.cancellable) is None:
                missing.append(probe)
                probe = probe.get_parent()
            for made in reversed(missing):
                try:
                    made.make_directory(self.cancellable)
                except GLib.Error as error:
                    # Made by someone else meanwhile is as good
                    if not has_code(error, Gio.IOErrorEnum.EXISTS):
                        raise
                    continue
                self._parts.append(undo.created(made))
        except GLib.Error as error:
            if is_cancelled(error):
                raise _Cancelled from None
            raise OperationError.from_glib(error, folder, "create folder") from error

    def _inner(self, kind, source, folder):
        """Run a move, copy or trash of one file here, as an ordinary job that nobody is
        asked in. Returns where the file is after it (None after a trash)."""
        job = Job(kind, [source], folder)
        job.cancellable = self.cancellable
        job._conflict_default = Reply(Answer.KEEP_BOTH)
        job._skip_all_errors = True
        job.run()
        if job.undo is not None:
            self._parts.append(job.undo)
        if job.state is JobState.CANCELLED:
            raise _Cancelled
        if job.state is not JobState.DONE:
            # One file, so one error: the first says what went wrong with it
            self.errors += job.errors[1:]
            raise job.errors[0] if job.errors else OperationError.at(source, kind.value, "failed")
        if kind is JobKind.TRASH:
            return None
        placed = _placed(job, source, folder)
        self.placed.append(placed)
        return placed

    def _undo_record(self):
        parts = tuple(part for part in self._parts if part is not None and not part.empty)
        if not parts:
            return None
        label = locations.display_name(self.folder)
        return undo.Record(undo.Kind.RULES, (), self._since, parts=parts, label=label)

    def _finish(self, cancelled):
        if not cancelled and not self.errors and not self._complete:
            # Unreachable by design, but `done` must never be a guess
            self.errors.append(OperationError("", "rules", "did not finish"))
        total = len(self.plan.files)
        if cancelled:
            state = JobState.CANCELLED
            self.summary = f"Cancelled after {self.acted} of {_files(total)}"
        elif self.errors:
            state = JobState.FAILED
            failed = total - self.acted
            self.summary = f"Rules acted on {self.acted} of {_files(total)}, {failed} failed"
        else:
            state = JobState.DONE
            self.summary = f"Rules acted on {_files(self.acted)}"
        self.progress = replace(self.progress, current="")
        self.undo = self._undo_record()
        self._set_state(state)


class RulesUndoJob(Job):
    """Take back a run of rules: what it did, last first. A move goes back, a trashed
    file is restored, a rename is renamed back and a folder it made is removed if it is
    still empty. Nothing is replaced and nobody is asked: what cannot go back is an
    error naming the file."""

    def __init__(self, record, *, on_change=None):
        super().__init__(JobKind.RULES, [], on_change=on_change)
        self.record = record
        self.is_undo = True
        # Where taking back puts files: where they were before the run
        self.placed = [was for part in record.parts for _now, was in part.pairs if was is not None]
        self._taken_back = 0
        self._complete = False

    @property
    def can_retry(self):
        return False

    def retry(self):
        return None

    def _work(self):
        parts = list(reversed(self.record.parts))
        self._set_progress(files_total=len(parts), planned=True)
        self._set_state(JobState.RUNNING)
        for part in parts:
            self._check_cancelled()
            self._take_back(part)
            self._set_progress(files_done=self.progress.files_done + 1)
        self._complete = True

    def _take_back(self, part):
        if part.kind is undo.Kind.RENAME:
            _changed, errors = undo.rename_back(part)
        elif part.kind is undo.Kind.CREATE:
            # A folder that holds something stays, and that is no failure: what a copy
            # put there is not taken back, and neither is what anyone else did
            errors = [] if _holds_anything(part.pairs[0][0]) else undo.remove_created(part)[1]
        else:
            job = Job.undo_of(part)
            job.cancellable = self.cancellable
            job.run()
            if job.state is JobState.CANCELLED:
                raise _Cancelled
            errors = job.errors
        self.errors += errors
        if not errors:
            self._taken_back += 1

    def _finish(self, cancelled):
        if not cancelled and not self.errors and not self._complete:
            self.errors.append(OperationError("", "rules", "did not finish"))
        total = len(self.record.parts)
        what = f"folder rules in “{self.record.label}”"
        if cancelled:
            state = JobState.CANCELLED
            self.summary = f"Cancelled taking back {what}"
        elif self.errors:
            state = JobState.FAILED
            self.summary = f"Took back {self._taken_back} of {total} actions of {what}"
        else:
            state = JobState.DONE
            self.summary = f"Took back {what}"
        self.progress = replace(self.progress, current="")
        self.undo = None
        self._set_state(state)


def _placed(job, source, folder):
    """Where a move or copy of one file put it: under the name it got there, which
    "Keep both" may have changed."""
    if job.undo is not None and job.undo.pairs:
        return job.undo.pairs[0][0]
    for step in job._steps:
        if step.done and step.parent is None:
            return step.dest
    return folder.get_child(source.get_basename())


def _holds_anything(folder):
    try:
        enumerator = folder.enumerate_children("standard::name", Gio.FileQueryInfoFlags.NONE, None)
    except GLib.Error:
        return False
    try:
        return enumerator.next_file(None) is not None
    except GLib.Error:
        return False
    finally:
        enumerator.close(None)


def _files(count):
    return "1 file" if count == 1 else f"{count} files"
