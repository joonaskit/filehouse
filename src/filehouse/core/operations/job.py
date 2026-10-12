"""A job: one user action that changes files, from planning to its final state.

`Job.run()` blocks and does all the work on the thread that calls it. It
reports through `on_change(job)`, which is called on that thread (and, for a
job cancelled while it is still queued, on the thread that cancelled it).
"""

import itertools
import logging
import threading
from dataclasses import dataclass, replace
from enum import Enum

from gi.repository import Gio, GLib

from filehouse.core.errors import OperationError, is_cancelled
from filehouse.core.operations import conflicts, copy, delete, plan, trash
from filehouse.core.operations.conflicts import Answer
from filehouse.core.operations.plan import StepKind

log = logging.getLogger(__name__)

_ids = itertools.count(1)


class JobKind(Enum):
    COPY = "copy"
    MOVE = "move"
    TRASH = "trash"
    RESTORE = "restore"
    DELETE = "delete"


class JobState(Enum):
    QUEUED = "queued"
    PLANNING = "planning"
    RUNNING = "running"
    WAITING = "waiting"  # for an answer from the user
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"

    @property
    def is_final(self):
        return self in (JobState.DONE, JobState.CANCELLED, JobState.FAILED)


@dataclass(frozen=True, slots=True)
class JobProgress:
    files_done: int = 0
    files_total: int = 0
    bytes_done: int = 0
    bytes_total: int = 0
    current: str = ""  # name of the item being worked on
    planned: bool = False

    @property
    def fraction(self):
        """0.0 to 1.0, or None while the totals aren't known. 1.0 does not mean done:
        only the job's state does."""
        if not self.planned:
            return None
        if self.bytes_total:
            return min(1.0, self.bytes_done / self.bytes_total)
        if self.files_total:
            return self.files_done / self.files_total
        return None


class _Cancelled(Exception):
    pass


_VERBS = {
    JobKind.COPY: "Copied",
    JobKind.MOVE: "Moved",
    JobKind.TRASH: "Moved",
    JobKind.RESTORE: "Restored",
    JobKind.DELETE: "Deleted",
}


class Job:
    """Copy or move `sources` into the folder `destination`, or trash or delete them.

    A restore takes trashed items as `sources` and, in `targets`, where each one
    goes back to.

    `prompt(question)` answers the questions in `conflicts`. Without one, nothing
    is ever replaced, a failed item is skipped and reported, and a permanent
    delete is refused.
    """

    def __init__(
        self, kind, sources, destination=None, *, targets=None, prompt=None, on_change=None
    ):
        self.id = next(_ids)
        self.kind = kind
        self.sources = list(sources)
        self.destination = destination
        self.cancellable = Gio.Cancellable()
        self.on_change = on_change
        self.progress = JobProgress()
        self.errors = []  # every OperationError, in the order they happened
        self.summary = ""  # the final text, e.g. "Cancelled after 5 of 12 files"

        self._prompt = prompt
        self._state = JobState.QUEUED
        self._started = False
        self._lock = threading.Lock()
        self._items = [(source, destination, source.get_basename()) for source in self.sources]
        if targets is not None:
            self._items = [
                (source, target.get_parent(), target.get_basename())
                for source, target in zip(self.sources, targets, strict=True)
            ]
        self._steps = []
        self._bytes_finished = 0
        self._conflict_default = None
        self._no_trash_default = None
        self._skip_all_errors = False

    @property
    def state(self):
        return self._state

    @property
    def started(self):
        """Whether the job ever ran. False for one cancelled while it was still queued."""
        return self._started

    @property
    def error(self):
        """The first error, or None."""
        return self.errors[0] if self.errors else None

    def cancel(self):
        """Ask the job to stop. It reports `cancelled` once the work has actually stopped."""
        self.cancellable.cancel()
        with self._lock:
            if self._state is not JobState.QUEUED:
                return
            self._state = JobState.CANCELLED
            self.summary = "Cancelled before it started"
        self._notify()

    @property
    def can_retry(self):
        return self._state is JobState.FAILED and any(step.failed for step in self._steps)

    def retry(self):
        """A new job for the items this one failed on, or None when there are none."""
        if not self.can_retry:
            return None
        items = []
        for step in self._steps:
            if not step.failed:
                continue
            if step.kind is StepKind.FINISH_DIR:
                step = step.parent
            folder = step.parent.dest if step.parent is not None else step.folder
            items.append((step.source, folder, step.name))
        job = Job(self.kind, [], self.destination, prompt=self._prompt, on_change=self.on_change)
        job.sources = [source for source, _folder, _name in items]
        job._items = items
        return job

    def run(self):
        """Do the whole job. Returns once it is done, cancelled or failed."""
        with self._lock:
            if self._state is not JobState.QUEUED:
                return
            self._started = True
            self._state = JobState.PLANNING
        self._notify()

        cancelled = False
        try:
            self._work()
        except _Cancelled:
            cancelled = True
        except OperationError as error:
            self.errors.append(error)
        except GLib.Error as error:
            cancelled = is_cancelled(error)
            if not cancelled:
                log.exception("job %d stopped", self.id)
                self.errors.append(OperationError("", self.kind.value, error.message))
        except Exception as error:
            log.exception("job %d stopped", self.id)
            self.errors.append(OperationError("", self.kind.value, f"internal error: {error}"))
        self._finish(cancelled)

    # Planning

    def _work(self):
        if self.kind is JobKind.DELETE:
            self._confirm_delete()
        if self._is_transfer:
            for source, folder, _name in self._items:
                plan.check_not_inside(source, folder, self.kind.value)
        for source, folder, name in self._items:
            self._check_cancelled()
            self._steps += self._plan_item(source, folder, name)
        self._recount(planned=True)
        self._set_state(JobState.RUNNING)

        # Answer what planning found before the long part starts
        for step in list(self._steps):
            if step.existing is not None and not step.blocked() and not step.under_fresh():
                self._attempt(step, self._planned_conflict)

        index = 0
        while index < len(self._steps):
            step = self._steps[index]
            if step.done or step.failed or step.left_out or step.blocked():
                index += 1
                continue
            if step.kind is StepKind.DELETE and step.incomplete:
                # A folder something couldn't be deleted from stays
                index += 1
                continue
            self._set_progress(current=step.source.get_basename())
            expansion = self._attempt(step, self._execute)
            if expansion is not None:
                self._steps[index : index + 1] = expansion
                self._recount()
                continue
            if not step.failed and not step.left_out:
                step.done = True
                self._bytes_finished += step.size
                self._recount()
            index += 1

    @property
    def _is_transfer(self):
        return self.kind in (JobKind.COPY, JobKind.MOVE)

    def _plan_item(self, source, folder, name, parent=None, rename=True):
        if self.kind is JobKind.TRASH:
            return plan.plan_trash(source)
        if self.kind is JobKind.RESTORE:
            return plan.plan_restore(source, folder, name, self.cancellable)
        return self._plan_tree(source, folder, name, parent, rename)

    def _plan_tree(self, source, folder, name, parent=None, rename=True):
        if self._is_transfer:
            return plan.plan_transfer(
                source,
                folder,
                name,
                self.kind is JobKind.MOVE,
                self.cancellable,
                parent=parent,
                rename=rename,
            )
        return plan.plan_delete(source, self.cancellable, parent=parent)

    def _confirm_delete(self):
        reply = self._ask(conflicts.ConfirmDelete(self.sources))
        if reply is None:
            raise OperationError("", "delete", "permanent delete needs to be confirmed")
        if reply.answer is not Answer.DELETE:
            self._cancel_from_answer()

    # Running

    def _attempt(self, step, action):
        """Run `action(step)` until it finishes, the step is skipped, or the job is cancelled."""
        while True:
            self._check_cancelled()
            try:
                return action(step)
            except conflicts.Exists as exists:
                try:
                    self._resolve_conflict(step, exists.file_type)
                except OperationError as failure:
                    error = failure
                else:
                    if step.left_out:
                        return None
                    continue
            except OperationError as failure:
                error = failure
            except GLib.Error as failure:
                if is_cancelled(failure):
                    raise _Cancelled from None
                error = OperationError.from_glib(failure, step.source, self._label(step))

            self._set_progress(bytes_done=self._bytes_finished)
            if self._ask_about(error) is Answer.RETRY:
                continue
            step.failed = True
            for ancestor in step.ancestors():
                ancestor.incomplete = True
            self.errors.append(error)
            return None

    def _planned_conflict(self, step):
        if step.existing is not None:
            raise conflicts.Exists(step.existing)

    def _execute(self, step):
        """Run one step. Returns the steps to run in its place when it has to be expanded."""
        kind = step.kind
        if kind is StepKind.UNPLANNED:
            if self.kind is JobKind.RESTORE:
                steps = plan.plan_restore(step.source, step.folder, step.name, self.cancellable)
            else:
                steps = self._plan_tree(step.source, step.folder, step.name, step.parent)
            if len(steps) == 1 and steps[0].kind is StepKind.UNPLANNED:
                raise steps[0].error
            return steps
        if step.existing is not None and not step.under_fresh():
            raise conflicts.Exists(step.existing)

        move = self.kind is JobKind.MOVE
        if kind is StepKind.MAKE_DIR:
            copy.make_folder(step, self.cancellable)
        elif kind is StepKind.COPY:
            copy.transfer_file(step, move, self.cancellable, self._on_bytes)
        elif kind is StepKind.RENAME:
            if not copy.rename(step, self.cancellable):
                return self._plan_tree(step.source, step.folder, step.name, rename=False)
        elif kind is StepKind.FINISH_DIR:
            copy.finish_folder(step, move)
        elif kind is StepKind.TRASH:
            try:
                trash.trash(step, self.cancellable)
            except trash.NoTrash:
                return self._no_trash(step)
        elif kind is StepKind.RESTORE:
            copy.restore(step, self.cancellable)
        elif kind is StepKind.DELETE:
            if not delete.delete(step, self.cancellable):
                return plan.plan_delete(step.source, self.cancellable, walk=True)
        return None

    def _label(self, step):
        if step.kind is StepKind.COPY:
            return self.kind.value
        return step.kind.value

    def _on_bytes(self, current):
        self._set_progress(bytes_done=self._bytes_finished + current)

    # Questions

    def _resolve_conflict(self, step, existing):
        """Decide what to do about `existing` at the step's destination, and mark the step."""
        dest = step.dest
        dest_is_dir = existing == Gio.FileType.DIRECTORY
        if step.kind is StepKind.MAKE_DIR and dest_is_dir and step.merges():
            step.overwrite = True
            step.existing = None
            return

        label = self._label(step)
        same_file = step.source.equal(dest)
        # A restore never replaces: what is there now may be newer than what was trashed
        restoring = step.kind is StepKind.RESTORE
        can_replace = step.is_dir == dest_is_dir and not same_file and not restoring
        reply = self._conflict_default
        if reply is None or (reply.answer is Answer.REPLACE and not can_replace):
            reply = self._ask(
                conflicts.Conflict(step.source, dest, step.is_dir, dest_is_dir, can_replace)
            )
            if reply is None:
                raise OperationError.at(step.source, label, "already exists at the destination")
            if reply.apply_to_all:
                self._conflict_default = replace(reply, apply_to_all=False)

        if reply.answer is Answer.REPLACE:
            if same_file:
                raise OperationError.at(step.source, label, "cannot replace a file with itself")
            if restoring:
                raise OperationError.at(step.source, label, "already exists where it was")
            if not can_replace:
                kinds = ("a folder", "a file") if step.is_dir else ("a file", "a folder")
                raise OperationError.at(
                    step.source, label, f"cannot replace {kinds[1]} with {kinds[0]}"
                )
            step.overwrite = True
        elif reply.answer is Answer.KEEP_BOTH:
            folder = dest.get_parent()
            step.name = conflicts.free_name(folder, step.name, step.is_dir, self.cancellable)
            step.fresh = True
        elif reply.answer is Answer.SKIP:
            step.left_out = True
            self._recount()
        else:
            self._cancel_from_answer()
        step.existing = None

    def _ask_about(self, error):
        """Skip or Retry for a failed step. Cancel doesn't return."""
        if self._skip_all_errors:
            return Answer.SKIP
        reply = self._ask(conflicts.Failure(error))
        if reply is None:
            return Answer.SKIP
        if reply.answer is Answer.RETRY:
            return Answer.RETRY
        if reply.answer is Answer.SKIP:
            self._skip_all_errors = reply.apply_to_all
            return Answer.SKIP
        self._cancel_from_answer()

    def _no_trash(self, step):
        """The item can't be trashed where it is. Deleting it instead is the user's choice only."""
        reply = self._no_trash_default or self._ask(conflicts.TrashUnsupported(step.source))
        if reply is None:
            raise OperationError.at(step.source, "trash", "cannot be moved to the trash here")
        if reply.apply_to_all:
            self._no_trash_default = replace(reply, apply_to_all=False)
        if reply.answer is Answer.DELETE:
            return plan.plan_delete(step.source, self.cancellable)
        if reply.answer is Answer.SKIP:
            step.left_out = True
            self._recount()
            return None
        self._cancel_from_answer()

    def _ask(self, question):
        """The user's Reply, or None when nobody can be asked."""
        if self._prompt is None:
            return None
        before = self._state
        question.job = self
        self._set_state(JobState.WAITING)
        try:
            reply = self._prompt(question)
            if reply is None:
                reply = question.wait(self.cancellable)
        finally:
            self._set_state(before)
        self._check_cancelled()
        reply = conflicts.as_reply(reply)
        if reply.answer not in question.answers and not isinstance(question, conflicts.Conflict):
            raise ValueError(f"{reply.answer} does not answer {type(question).__name__}")
        return reply

    def _cancel_from_answer(self):
        self.cancellable.cancel()
        raise _Cancelled

    def _check_cancelled(self):
        if self.cancellable.is_cancelled():
            raise _Cancelled

    # Reporting

    def _counted(self):
        return [step for step in self._steps if step.counted and not step.excluded()]

    def _recount(self, planned=None):
        counted = self._counted()
        self._set_progress(
            files_done=sum(step.done for step in counted),
            files_total=len(counted),
            bytes_done=self._bytes_finished,
            bytes_total=sum(step.size for step in counted),
            planned=self.progress.planned if planned is None else planned,
        )

    def _set_progress(self, **changes):
        progress = replace(self.progress, **changes)
        if progress != self.progress:
            self.progress = progress
            self._notify()

    def _set_state(self, state):
        if state is not self._state:
            self._state = state
            self._notify()

    def _notify(self):
        if self.on_change is None:
            return
        try:
            self.on_change(self)
        except Exception:
            log.exception("on_change failed for job %d", self.id)

    def _finish(self, cancelled):
        planned = self.progress.planned
        complete = planned and all(step.done or step.excluded() for step in self._steps)
        if not cancelled and not self.errors and not complete:
            # Unreachable by design, but `done` must never be a guess
            self.errors.append(OperationError("", self.kind.value, "did not finish"))

        counted = self._counted()
        done, total = sum(step.done for step in counted), len(counted)
        noun = "file"
        whole_items = self.kind in (JobKind.TRASH, JobKind.RESTORE)
        if whole_items or any(step.is_dir or step.whole for step in counted):
            noun = "item"
        if not planned:
            total, noun = len(self._items), "item"
        of_total = f"{done} of {total} {noun}{'' if total == 1 else 's'}"
        verb = _VERBS[self.kind]
        where = " to the trash" if self.kind is JobKind.TRASH else ""

        if cancelled:
            state = JobState.CANCELLED
            if self.kind is JobKind.TRASH:
                self.summary = f"{verb} {of_total}{where}"
            else:
                self.summary = f"Cancelled after {of_total}"
        elif self.errors:
            state = JobState.FAILED
            if not planned:
                self.summary = str(self.errors[0])
            elif total > done:
                self.summary = f"{verb} {of_total}{where}, {total - done} skipped"
            else:
                self.summary = f"{verb} {of_total}{where}, {len(self.errors)} failed"
        else:
            state = JobState.DONE
            self.summary = f"{verb} {done} {noun}{'' if done == 1 else 's'}{where}"

        self.progress = replace(self.progress, current="", bytes_done=self._bytes_finished)
        self._set_state(state)
