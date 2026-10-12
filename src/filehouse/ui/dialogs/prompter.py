"""Shows the questions jobs ask, one at a time, and hands back the answers."""

from collections import deque

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid

from filehouse.core.operations import Answer, JobState, Reply
from filehouse.ui.dialogs.questions import box_for


class Prompter(QObject):
    """The one listener of `JobModel.question_asked`."""

    def __init__(self, jobs, parent=None):
        super().__init__(parent)
        self._jobs = jobs
        self._waiting = deque()
        self.current = None  # the question's box on screen
        self._current_job = None
        jobs.question_asked.connect(self._on_asked)
        jobs.job_changed.connect(self._on_job_changed)

    def _on_asked(self, job, question):
        self._waiting.append((job, question))
        self._show_next()

    def _show_next(self):
        while self.current is None and self._waiting:
            job, question = self._waiting.popleft()
            if job.state is not JobState.WAITING:
                # Cancelled before its turn came
                continue
            box = box_for(question, self._parent_for(job))
            box.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            box.finished.connect(lambda _result, box=box: self._on_finished(box))
            box.destroyed.connect(lambda _box=None, question=question: self._on_gone(question))
            self.current = box
            self._current_job = job
            box.open()

    def _parent_for(self, job):
        origin = self._jobs.origin(job)
        if isinstance(origin, QWidget) and isValid(origin) and origin.isVisible():
            return origin
        return QApplication.activeWindow()

    def _on_finished(self, box):
        if box is not self.current:
            return
        self.current = None
        box.question.answer(box.reply())
        self._show_next()

    def _on_gone(self, question):
        """A box was destroyed. If it never got its answer, its window was closed under it."""
        job = self._current_job
        if self.current is None or self.current.question is not question:
            return
        self.current = None
        # Ask again, on a window that is still there
        self._waiting.appendleft((job, question))
        self._show_next()

    def _on_job_changed(self, job):
        # The job moved on without an answer: it was cancelled from the panel or the island
        box = self.current
        if box is not None and box.question.job is job and job.state is not JobState.WAITING:
            self.current = None
            box.question.answer(Reply(Answer.CANCEL))
            box.close()
            self._show_next()
