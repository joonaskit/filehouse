"""The running operations as one line in the status bar. Click it for the panel."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton

from filehouse.core.operations import JobState
from filehouse.integration import style
from filehouse.qt_models import job_model
from filehouse.ui.progress_bar import ProgressBar

_BAR_WIDTH = 120
_COMPACT_BAR_WIDTH = 64


class JobStatus(QToolButton):
    """Hidden while there are no jobs. What it says:

    - one job running: what it does, a bar and the percentage;
    - several running: how many, and how far they are together;
    - none running: that some failed, or else how the last one ended."""

    def __init__(self, jobs, parent=None):
        super().__init__(parent)
        self.setObjectName("jobStatus")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Show the operations")
        self._jobs = jobs
        self._compact = False

        self.title = QLabel()
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.bar = ProgressBar()
        self.bar.setFixedSize(style.px(_BAR_WIDTH), style.px(4))
        self.percent = QLabel()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(style.px(6), style.px(2), style.px(6), style.px(2))
        layout.setSpacing(style.px(4))
        for widget in (self.title, self.bar, self.percent):
            # A click anywhere on the line is a click on the line
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            layout.addWidget(widget)

        jobs.job_added.connect(self.refresh)
        jobs.job_changed.connect(self.refresh)
        jobs.job_removed.connect(self.refresh)
        self.refresh()

    def sizeHint(self):
        # Of what is in it: a tool button only knows about its own text and icon
        return self.layout().sizeHint().expandedTo(QSize(0, style.px(24)))

    def minimumSizeHint(self):
        return self.sizeHint()

    def set_compact(self, on):
        """For a narrow window: one word for the job, and a shorter bar."""
        if on == self._compact:
            return
        self._compact = on
        self.bar.setFixedWidth(style.px(_COMPACT_BAR_WIDTH if on else _BAR_WIDTH))
        self.refresh()

    def refresh(self, *_args):
        jobs = self._jobs.jobs
        active = [job for job in jobs if not job.state.is_final]
        failed = sum(job.state is JobState.FAILED for job in jobs)
        self.setVisible(bool(jobs))
        colour = None
        if len(active) == 1:
            job = active[0]
            text = job_model.verb(job) if self._compact else job_model.short_heading(job)
        elif active:
            text = f"{len(active)} operations"
        elif failed:
            text = "1 operation failed" if failed == 1 else f"{failed} operations failed"
            colour = "danger"
        elif jobs:
            # Ended, and on show for a moment longer
            text = jobs[-1].summary if len(jobs) == 1 else "Operations finished"
            if all(job.state is JobState.DONE for job in jobs):
                colour = "success"
        else:
            text = ""
        self.title.setText(text)
        self.title.setStyleSheet(f"color: {style.color(colour).name()};" if colour else "")
        self.setAccessibleName(text)

        self.bar.setVisible(bool(active))
        fraction = job_model.overall_fraction(active)
        # Busy without one: nothing is known yet about how much there is to do
        self.bar.set_fraction(fraction)
        if fraction is None:
            self.percent.hide()
        else:
            # Never 100 %: all the bytes written is not yet done, only the job's state says that
            self.percent.setText(f"{min(99, int(fraction * 100))} %")
            self.percent.setVisible(bool(active))
        self.updateGeometry()
