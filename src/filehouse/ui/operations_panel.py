"""The strip at the bottom of a window that lists file operations.

It shows what `JobModel` holds and nothing more: every window's panel, and the
island, are views of the same jobs. It is closed until it is asked for from the
status bar, or until a job needs the user: a failure, or a question.
"""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from filehouse.core.operations import JobState
from filehouse.integration import style
from filehouse.qt_models import job_model
from filehouse.ui.dialogs.details import ErrorDetails

_MAX_HEIGHT = 220
_PROGRESS_STEPS = 1000


class JobRow(QWidget):
    def __init__(self, job, jobs, parent=None):
        super().__init__(parent)
        self.job = job
        self._jobs = jobs

        icon = QLabel()
        size = style.px(22)
        icon.setPixmap(QIcon.fromTheme(job_model.ICONS[job.kind]).pixmap(QSize(size, size)))
        self.title = QLabel()
        self.text = QLabel()
        self.text.setEnabled(False)
        for label in (self.title, self.text):
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(style.px(6))

        self.cancel = self._button("Cancel", lambda: jobs.cancel(job))
        self.retry = self._button("Retry", lambda: jobs.retry(job))
        self.details = self._button("Details", self._show_details)
        self.dismiss = self._button("Dismiss", lambda: jobs.dismiss(job))
        buttons = QHBoxLayout()
        buttons.setSpacing(style.px(4))
        for button in (self.cancel, self.retry, self.details, self.dismiss):
            buttons.addWidget(button)

        layout = QGridLayout(self)
        layout.setContentsMargins(style.px(10), style.px(6), style.px(10), style.px(6))
        layout.setHorizontalSpacing(style.px(10))
        layout.setVerticalSpacing(style.px(2))
        layout.addWidget(icon, 0, 0, 2, 1)
        layout.addWidget(self.title, 0, 1)
        layout.addWidget(self.text, 1, 1)
        layout.addWidget(self.bar, 2, 1)
        layout.addLayout(buttons, 0, 2, 3, 1, Qt.AlignmentFlag.AlignVCenter)
        layout.setColumnStretch(1, 1)
        self.refresh()

    def _button(self, text, slot):
        button = QToolButton()
        button.setText(text)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.clicked.connect(slot)
        return button

    def refresh(self):
        job = self.job
        state = job.state
        self.title.setText(job_model.title(job))
        self.title.setToolTip(self.title.text())
        self.text.setText(job_model.text(job, self._jobs.speed(job)))
        self.text.setToolTip(self.text.text())
        self.text.setVisible(bool(self.text.text()))

        fraction = job.progress.fraction
        self.bar.setVisible(not state.is_final)
        if fraction is None:
            # Busy indicator: the totals aren't known yet
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, _PROGRESS_STEPS)
            self.bar.setValue(round(fraction * _PROGRESS_STEPS))

        failed = state is JobState.FAILED
        self.cancel.setVisible(not state.is_final)
        # A cancel is a request: the row stays until the job says how it ended
        self.cancel.setEnabled(not job.cancellable.is_cancelled())
        self.retry.setVisible(job.can_retry)
        self.details.setVisible(failed)
        self.dismiss.setVisible(state.is_final)
        colour = {JobState.FAILED: "danger", JobState.DONE: "success"}.get(state)
        self.title.setStyleSheet(f"color: {style.color(colour).name()};" if colour else "")

    def _show_details(self):
        ErrorDetails(self.job, self.window()).open()


# A job in one of these needs the user, so the panel opens by itself
_NEEDS_THE_USER = (JobState.FAILED, JobState.WAITING)


class OperationsPanel(QFrame):
    """Shown when it is open and there are jobs. Click the header to close it."""

    def __init__(self, jobs, parent=None):
        super().__init__(parent)
        self.setObjectName("operations")
        self._jobs = jobs
        self._rows = {}
        self._open = False
        self._states = {}  # job id -> the state it was last seen in

        self._header = QToolButton()
        self._header.setObjectName("operationsHeader")
        self._header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._header.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._header.setArrowType(Qt.ArrowType.DownArrow)
        self._header.setToolTip("Hide the operations")
        self._header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._header.clicked.connect(lambda: self.set_open(False))

        self._list = QVBoxLayout()
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(0)
        self._list.addStretch(1)
        holder = QWidget()
        holder.setLayout(self._list)
        self._holder = holder
        self._scroll = QScrollArea()
        self._scroll.setWidget(holder)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._header)
        layout.addWidget(self._scroll)

        jobs.job_added.connect(self._add)
        jobs.job_changed.connect(self._change)
        jobs.job_removed.connect(self._remove)
        for job in jobs.jobs:
            self._add(job)
        self._update()

    def row(self, job):
        return self._rows.get(job.id)

    @property
    def is_open(self):
        return self._open

    def set_open(self, open_):
        """Show the list, or put it away. Without jobs there is nothing to show either way."""
        self._open = open_
        self._update()

    def toggle(self):
        self.set_open(not self._open)

    def _add(self, job):
        row = JobRow(job, self._jobs)
        self._rows[job.id] = row
        self._list.insertWidget(self._list.count() - 1, row)
        self._notice(job)
        self._update()

    def _change(self, job):
        row = self._rows.get(job.id)
        if row is not None:
            row.refresh()
            self._notice(job)
            self._update()

    def _remove(self, job):
        row = self._rows.pop(job.id, None)
        self._states.pop(job.id, None)
        if row is not None:
            self._list.removeWidget(row)
            row.deleteLater()
            self._update()

    def _notice(self, job):
        """Open when a job starts to need the user. Once per time it does: a panel
        the user has closed again stays closed."""
        before = self._states.get(job.id)
        self._states[job.id] = job.state
        if job.state is not before and job.state in _NEEDS_THE_USER:
            self._open = True

    def _update(self):
        rows = list(self._rows.values())
        if not rows:
            # The next job starts with the panel closed
            self._open = False
        self.setVisible(self._open and bool(rows))
        running = sum(not row.job.state.is_final for row in rows)
        failed = sum(row.job.state is JobState.FAILED for row in rows)
        if running:
            text = "1 operation running" if running == 1 else f"{running} operations running"
        elif failed:
            text = "1 operation failed" if failed == 1 else f"{failed} operations failed"
        else:
            text = "Operations finished"
        self._header.setText(text)
        # As tall as its rows, up to a limit; more rows than that scroll
        self._scroll.setFixedHeight(min(self._holder.sizeHint().height(), style.px(_MAX_HEIGHT)))
