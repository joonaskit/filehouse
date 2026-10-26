"""Tells the user that a job ended while no window of the app was there to show it.

Two parts, as for the island. `Notifier` talks D-Bus to the desktop's notification
service and knows nothing about jobs. `NotificationReporter` observes `JobModel`
and decides when a notification is worth sending: it holds no job logic.

Without a notification service nothing here does anything, and nothing here can
stop a job: every call is asynchronous and every failure is only logged.
"""

import logging
import time

from gi.repository import Gio, GLib
from PySide6.QtCore import QObject

from filehouse import APP_ID
from filehouse.core import locations
from filehouse.core.operations import JobKind, JobState
from filehouse.qt_models import job_model

log = logging.getLogger(__name__)

BUS_NAME = "org.freedesktop.Notifications"
OBJECT_PATH = "/org/freedesktop/Notifications"
INTERFACE = "org.freedesktop.Notifications"

_CALL_TIMEOUT_MS = 2000
URGENCY_NORMAL, URGENCY_CRITICAL = 1, 2
_DEFAULT_TIMEOUT, _NEVER_EXPIRES = -1, 0
# With the island showing the job, a notification is for the ones the user may have
# looked away from: those that ran this long, in seconds
LONG_JOB_SECONDS = 10


class Notifier:
    """The freedesktop notifications service over D-Bus. Create it on the main thread."""

    def __init__(self, connection=None):
        self._connection = connection

    def start(self):
        if self._connection is None:
            Gio.bus_get(Gio.BusType.SESSION, None, self._on_bus)

    def _on_bus(self, _source, result):
        try:
            self._connection = Gio.bus_get_finish(result)
        except GLib.Error as error:
            log.debug("no session bus, so no notifications: %s", error.message)

    def notify(self, summary, body, icon, urgent=False, done=None):
        """Show a notification. A critical one stays until the user dismisses it.
        `done(ok)` is called when the service has answered, or when there is none."""
        if self._connection is None:
            if done is not None:
                done(False)
            return
        hints = {
            "urgency": GLib.Variant("y", URGENCY_CRITICAL if urgent else URGENCY_NORMAL),
            "desktop-entry": GLib.Variant("s", APP_ID),
        }
        parameters = GLib.Variant(
            "(susssasa{sv}i)",
            (
                APP_ID,
                0,
                icon,
                summary,
                body,
                [],
                hints,
                _NEVER_EXPIRES if urgent else _DEFAULT_TIMEOUT,
            ),
        )
        self._connection.call(
            BUS_NAME,
            OBJECT_PATH,
            INTERFACE,
            "Notify",
            parameters,
            None,
            Gio.DBusCallFlags.NONE,
            _CALL_TIMEOUT_MS,
            None,
            self._on_reply,
            done,
        )

    def _on_reply(self, connection, result, done):
        ok = True
        try:
            connection.call_finish(result)
        except GLib.Error as error:
            ok = False
            log.debug("could not show a notification: %s", error.message)
        if done is not None:
            done(ok)

    def flush(self):
        """Write what is queued to the bus. For the moment before the process ends."""
        if self._connection is None:
            return
        try:
            self._connection.flush_sync(None)
        except GLib.Error as error:
            log.debug("could not flush the notifications: %s", error.message)


class NotificationReporter(QObject):
    """Sends a notification for a job that ends while no window is visible.

    - With no island, every job that finished or failed.
    - With the island, only a job that ran long enough for the user to have looked
      away. A failure is not sent: the island keeps it until it is dismissed.
    - Never a job the user cancelled, or one that never ran.
    - A run of a folder's rules is told whenever it acted, island or not: nobody started
      it, so nobody is waiting to see it end (#87). A failure that is the same as the
      last of the same folder's rules (the same files, the same reasons) is told once.

    `window_visible()` says whether any window of the app is on screen, and
    `island_available()` whether the island is showing jobs. `enabled()` says whether the
    user wants notifications at all, and `long_job_seconds` is a number or a function.
    """

    def __init__(
        self,
        jobs,
        notifier,
        window_visible,
        island_available,
        parent=None,
        long_job_seconds=LONG_JOB_SECONDS,
        clock=time.monotonic,
        enabled=lambda: True,
    ):
        super().__init__(parent)
        self._notifier = notifier
        self._window_visible = window_visible
        self._island_available = island_available
        self._long = long_job_seconds
        self._clock = clock
        self._enabled = enabled
        self._started = {}  # job id -> when it was first seen running
        self._notified = set()
        # The failures of runs of rules that were told: (folder URI, errors)
        self._rule_failures = set()
        jobs.job_changed.connect(self._on_job_changed)
        jobs.job_removed.connect(self._on_job_removed)

    def _on_job_changed(self, job):
        state = job.state
        if not state.is_final:
            if state is not JobState.QUEUED:
                self._started.setdefault(job.id, self._clock())
            return
        if job.id in self._notified:
            return
        self._notified.add(job.id)
        started = self._started.pop(job.id, None)
        if (
            state is JobState.CANCELLED
            or not job.started
            or self._window_visible()
            or not self._enabled()
        ):
            return
        if job.kind is JobKind.RULES and not job.is_undo:
            if self._worth_telling_rules(job):
                self._send(job)
            return
        if self._island_available():
            if state is JobState.FAILED:
                return
            if started is None or self._clock() - started < self._seconds():
                return
        self._send(job)

    def _worth_telling_rules(self, job):
        if job.state is not JobState.FAILED:
            return bool(job.acted)
        failure = (locations.uri(job.destination), tuple(str(error) for error in job.errors))
        if failure in self._rule_failures:
            return False
        self._rule_failures.add(failure)
        return True

    def _seconds(self):
        return self._long() if callable(self._long) else self._long

    def _on_job_removed(self, job):
        self._started.pop(job.id, None)
        self._notified.discard(job.id)

    def _send(self, job):
        if job.state is JobState.FAILED:
            self._notifier.notify(
                job.summary or job_model.heading(job),
                job_model.error_text(job),
                "dialog-error",
                urgent=True,
            )
        else:
            self._notifier.notify(job.summary, "", job_model.ICONS[job.kind])
