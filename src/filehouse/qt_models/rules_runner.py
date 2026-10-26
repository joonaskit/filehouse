"""The rules of folders running by themselves (#87): when the process starts, once a
day while it runs, and when a file arrives in a folder that has rules (#88).

What a folder's rules would do is found on a worker (`automations.plan_run` reads the
folder), and done as one job of `JobModel`. Nobody is asked: the rules were seen in
their preview when they were saved. A folder that is gone, or is not on this computer,
is passed over without a word.
"""

import time
from dataclasses import dataclass, field

from gi.repository import Gio, GLib
from PySide6.QtCore import QObject, QTimer

from filehouse.core import automations, locations
from filehouse.core.operations import JobKind
from filehouse.qt_models.workers import Background

# How often the clock is looked at, to find that a day has passed: a laptop that slept
# through the moment catches up when it wakes, and a long run of the process does not drift
CHECK_MS = 5 * 60 * 1000
# How long what arrived in a folder waits before the rules look at it: long enough for
# what was just written to count as settled
ARRIVAL_MS = (automations.SETTLE_SECONDS + 1) * 1000
# How long a place that a run of rules put a file at is remembered, to pass over the
# file appearing there
PLACED_SECONDS = 10 * 60


@dataclass
class _Watch:
    monitor: object
    handler: int
    timer: QTimer
    # The names that arrived since the rules last looked
    names: set = field(default_factory=set)


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
        arrival_ms=ARRIVAL_MS,
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
        self._arrival_ms = arrival_ms
        # The folders followed for arrivals, by URI
        self._watches = {}
        # {uri: when} of where runs of rules put files: their arrival is no new file
        self._placed = {}
        jobs.job_changed.connect(self._on_job_changed)

    def start(self):
        """Run the rules now, for what came while the process was not there to see it,
        and then once a day, and follow the folders for what arrives."""
        self.run_all()
        self._timer.start()
        self.watch()

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

    # Arrivals

    def watch(self):
        """Follow the folders that have rules, to run them on what arrives; called again
        when the rules or the switch change."""
        wanted = set()
        if self._enabled():
            for uri, rules in self._stored().items():
                if any(rule.enabled for rule in rules) and locations.from_uri(uri).is_native():
                    wanted.add(uri)
        for uri in set(self._watches) - wanted:
            self._unwatch(uri)
        for uri in wanted - set(self._watches):
            self._watch(uri)

    def _watch(self, uri):
        try:
            monitor = locations.from_uri(uri).monitor_directory(
                Gio.FileMonitorFlags.WATCH_MOVES, None
            )
        except GLib.Error:
            # Its rules still run at the start, once a day and by hand
            return
        handler = monitor.connect(
            "changed",
            lambda _monitor, file, other, event: self._on_changed(uri, file, other, event),
        )
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(self._arrival_ms)
        timer.timeout.connect(lambda: self._arrived(uri))
        self._watches[uri] = _Watch(monitor, handler, timer)

    def _unwatch(self, uri):
        watch = self._watches.pop(uri)
        watch.monitor.disconnect(watch.handler)
        watch.monitor.cancel()
        watch.timer.stop()
        watch.timer.deleteLater()

    def _on_changed(self, uri, file, other, event):
        events = Gio.FileMonitorEvent
        # Only a file appearing counts: a change to one already there is not an arrival
        if event in (events.CREATED, events.MOVED_IN):
            arrived = file
        elif event == events.RENAMED:
            arrived = other
        else:
            return
        watch = self._watches.get(uri)
        if watch is None or arrived is None:
            return
        name = arrived.get_basename()
        # Its rename to the final name is the arrival
        if automations.is_temporary(name):
            return
        watch.names.add(name)
        # Not restarted by later arrivals: a folder written to all the time still gets runs
        if not watch.timer.isActive():
            watch.timer.start()

    def _arrived(self, uri):
        """Run the rules of the folder at `uri` on the names that arrived there."""
        watch = self._watches.get(uri)
        if watch is None:
            return
        if uri in self._busy or self._rules_running():
            # Where a run puts files is known only once it ends
            watch.timer.start()
            return
        folder = locations.from_uri(uri)
        self._forget_placed()
        names = {
            name
            for name in watch.names
            if self._placed.pop(folder.get_child(name).get_uri(), None) is None
        }
        watch.names.clear()
        rules = self._stored().get(uri, [])
        if not names or not self._enabled() or not any(rule.enabled for rule in rules):
            return
        self._busy[uri] = None
        self._background.call(
            lambda: automations.plan_run(folder, rules, names=names),
            lambda plan: self._planned_arrivals(uri, folder, plan),
            lambda _error: self._busy.pop(uri, None),
        )

    def _planned_arrivals(self, uri, folder, plan):
        watch = self._watches.get(uri)
        if watch is not None and plan.still_changing:
            # Looked at again once they have settled
            watch.names.update(location.get_basename() for location in plan.still_changing)
            if not watch.timer.isActive():
                watch.timer.start()
        self._planned(uri, folder, plan)

    def _rules_running(self):
        return any(job.kind is JobKind.RULES and not job.state.is_final for job in self._jobs.jobs)

    def _forget_placed(self):
        now = self._clock()
        for uri, when in list(self._placed.items()):
            if now - when > PLACED_SECONDS:
                del self._placed[uri]

    def _planned(self, uri, folder, plan):
        if not plan.files or not self._enabled():
            self._busy.pop(uri, None)
            return
        self._busy[uri] = self._jobs.run_rules(folder, plan)

    def _on_job_changed(self, job):
        if not job.state.is_final:
            return
        for location in getattr(job, "placed", ()):
            self._placed[location.get_uri()] = self._clock()
        for uri, running in list(self._busy.items()):
            if running is job:
                del self._busy[uri]
