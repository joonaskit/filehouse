"""The rules of folders running by themselves (#87): when the process starts, and once a
day while it runs.

What a folder's rules would do is found on a worker (`automations.plan_run` reads the
folder), and done as one job of `JobModel`. Nobody is asked: the rules were seen in
their preview when they were saved. A folder that is gone, or is not on this computer,
is passed over without a word.
"""

import time

from PySide6.QtCore import QObject, QTimer

from filehouse.core import automations, locations
from filehouse.qt_models.workers import Background

# How often the clock is looked at, to find that a day has passed: a laptop that slept
# through the moment catches up when it wakes, and a long run of the process does not drift
CHECK_MS = 5 * 60 * 1000


class RulesRunner(QObject):
    """Runs the rules of every folder that has some. `stored()` gives {uri: rules},
    `enabled()` whether rules run at all, and `last_run()` and `set_last_run(when)`
    keep when they last ran by themselves, by `clock`."""

    def __init__(
        self,
        jobs,
        stored,
        enabled,
        last_run,
        set_last_run,
        parent=None,
        check_ms=CHECK_MS,
        clock=time.time,
    ):
        super().__init__(parent)
        self._jobs = jobs
        self._stored = stored
        self._enabled = enabled
        self._last_run = last_run
        self._set_last_run = set_last_run
        self._clock = clock
        # Planning only reads: a folder that does not answer holds up nothing else
        self._background = Background(self, in_turn=False)
        # The folders being planned or run, by URI: one run of a folder at a time
        self._busy = {}
        self._timer = QTimer(self)
        self._timer.setInterval(check_ms)
        self._timer.timeout.connect(self._check)
        jobs.job_changed.connect(self._on_job_changed)

    def start(self):
        """Run the rules now, for what came while the process was not there to see it,
        and then once a day."""
        self.run_all()
        self._timer.start()

    def run_all(self):
        if not self._enabled():
            return
        self._set_last_run(self._clock())
        for uri, rules in self._stored().items():
            self.run(uri, rules)

    def run(self, uri, rules):
        """Find what the rules of the folder at `uri` would do now, and do it."""
        if uri in self._busy or not any(rule.enabled for rule in rules):
            return
        folder = locations.from_uri(uri)
        if not folder.is_native():
            return
        self._busy[uri] = None
        self._background.call(
            lambda: automations.plan_run(folder, rules),
            lambda plan: self._planned(uri, folder, plan),
            # A folder that is gone or cannot be read: its rules wait for it
            lambda _error: self._busy.pop(uri, None),
        )

    def _check(self):
        if self._clock() - self._last_run() >= automations.DAY_SECONDS:
            self.run_all()

    def _planned(self, uri, folder, plan):
        if not plan.files or not self._enabled():
            self._busy.pop(uri, None)
            return
        self._busy[uri] = self._jobs.run_rules(folder, plan)

    def _on_job_changed(self, job):
        if not job.state.is_final:
            return
        for uri, running in list(self._busy.items()):
            if running is job:
                del self._busy[uri]
