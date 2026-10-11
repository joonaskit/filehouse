"""Shows file operations in the Quickshell Island, through its activities API.

Two parts. `IslandClient` talks D-Bus and knows nothing about jobs.
`IslandReporter` observes `JobModel` and translates: it holds no job logic, and
everything it shows is also in the app's own operations panel.

Without an island nothing here does anything, and nothing here can stop a job:
every call is asynchronous and every failure is only logged.
"""

import logging
import time
from enum import Enum

from gi.repository import Gio, GLib
from PySide6.QtCore import QObject, QTimer, Signal

from filehouse.core.operations import JobState
from filehouse.qt_models import job_model

log = logging.getLogger(__name__)

BUS_NAME = "org.quickshell.IslandActivities"
OBJECT_PATH = "/org/quickshell/IslandActivities"
INTERFACE = "org.quickshell.IslandActivities1"
# The only version of the API this client knows
VERSION = 1
_ERROR_PREFIX = f"{BUS_NAME}.Error."

_FIELD_TYPES = {
    "title": "s",
    "text": "s",
    "icon": "s",
    "progress": "d",
    "priority": "s",
    "state": "s",
    "error": "s",
    "actions": "a(ss)",
}

_CALL_TIMEOUT_MS = 2000
# The API asks for a few progress updates a second at most
_UPDATE_INTERVAL_MS = 250
_RETRY_MS = 1000
_MAX_RETRIES = 5


class IslandClient:
    """The activities API over D-Bus. Create it on the main thread.

    Set the `on_...` callbacks, then call `start()`. `available` is true while
    an island with the right API version is on the bus.
    """

    def __init__(self, connection=None):
        self._connection = connection
        self._watch = 0
        self._subscriptions = []
        self._owner = None
        self.available = False
        self.on_available = lambda: None
        self.on_unavailable = lambda: None
        self.on_action = lambda activity_id, action: None
        self.on_dismissed = lambda activity_id: None

    def start(self):
        if self._connection is not None:
            self._watch_name()
        else:
            Gio.bus_get(Gio.BusType.SESSION, None, self._on_bus)

    def close(self):
        if self._connection is None:
            return
        if self._watch:
            Gio.bus_unwatch_name(self._watch)
            self._watch = 0
        for subscription in self._subscriptions:
            self._connection.signal_unsubscribe(subscription)
        self._subscriptions = []
        self.available = False

    def show(self, activity_id, fields, done=None):
        self._call("Show", activity_id, fields, done)

    def update(self, activity_id, fields, done=None):
        self._call("Update", activity_id, fields, done)

    def dismiss(self, activity_id, done=None):
        self._call("Dismiss", activity_id, None, done)

    def _on_bus(self, _source, result):
        try:
            self._connection = Gio.bus_get_finish(result)
        except GLib.Error as error:
            log.debug("no session bus, so no island: %s", error.message)
            return
        self._watch_name()

    def _watch_name(self):
        connection = self._connection
        # Broadcast to the whole bus: the reporter ignores ids that aren't its own
        self._subscriptions = [
            connection.signal_subscribe(
                BUS_NAME, INTERFACE, signal, OBJECT_PATH, None, Gio.DBusSignalFlags.NONE, handler
            )
            for signal, handler in (
                ("ActionInvoked", self._on_action_signal),
                ("Dismissed", self._on_dismissed_signal),
            )
        ]
        self._watch = Gio.bus_watch_name_on_connection(
            connection,
            BUS_NAME,
            Gio.BusNameWatcherFlags.NONE,
            self._on_appeared,
            self._on_vanished,
        )

    def _on_appeared(self, connection, _name, owner):
        # A restarted island is a new owner, and has forgotten everything
        self._set_available(False)
        self._owner = owner
        connection.call(
            BUS_NAME,
            OBJECT_PATH,
            "org.freedesktop.DBus.Properties",
            "Get",
            GLib.Variant("(ss)", (INTERFACE, "Version")),
            GLib.VariantType("(v)"),
            Gio.DBusCallFlags.NO_AUTO_START,
            _CALL_TIMEOUT_MS,
            None,
            self._on_version,
            owner,
        )

    def _on_version(self, connection, result, owner):
        try:
            (version,) = connection.call_finish(result).unpack()
        except GLib.Error as error:
            log.debug("could not read the island's API version: %s", error.message)
            return
        if owner != self._owner:
            return
        if version != VERSION:
            # Not an error: this client just stays silent
            log.debug("island speaks API version %s, this client version %s", version, VERSION)
            return
        self._set_available(True)

    def _on_vanished(self, _connection, _name):
        self._owner = None
        self._set_available(False)

    def _set_available(self, available):
        if available == self.available:
            return
        self.available = available
        (self.on_available if available else self.on_unavailable)()

    def _on_action_signal(self, _connection, _sender, _path, _interface, _signal, parameters):
        self.on_action(*parameters.unpack())

    def _on_dismissed_signal(self, _connection, _sender, _path, _interface, _signal, parameters):
        self.on_dismissed(*parameters.unpack())

    def _call(self, method, activity_id, fields, done):
        if not self.available:
            return
        if fields is None:
            parameters = GLib.Variant("(s)", (activity_id,))
        else:
            variants = {
                name: GLib.Variant(_FIELD_TYPES[name], value) for name, value in fields.items()
            }
            parameters = GLib.Variant("(sa{sv})", (activity_id, variants))
        self._connection.call(
            BUS_NAME,
            OBJECT_PATH,
            INTERFACE,
            method,
            parameters,
            None,
            Gio.DBusCallFlags.NO_AUTO_START,
            _CALL_TIMEOUT_MS,
            None,
            self._on_reply,
            (method, activity_id, done),
        )

    def _on_reply(self, connection, result, context):
        method, activity_id, done = context
        code = None
        try:
            connection.call_finish(result)
        except GLib.Error as error:
            remote = Gio.DBusError.get_remote_error(error) or ""
            code = remote.removeprefix(_ERROR_PREFIX) if remote else "Failed"
            log.debug("island refused %s for %s: %s", method, activity_id, error.message)
        if done is not None:
            done(code)


class _Phase(Enum):
    UNSENT = "unsent"  # the island doesn't have it (yet, or any more)
    SHOWING = "showing"  # Show was sent and its reply hasn't come
    SHOWN = "shown"
    GONE = "gone"  # dismissed or timed out in the island: leave it alone


class _Activity:
    def __init__(self, activity_id, job):
        self.id = activity_id
        self.job = job
        self.phase = _Phase.UNSENT
        self.sent = {}  # the fields the island has, as far as we know
        self.last_sent = 0.0
        self.timer = None
        self.retries = 0
        self.made_room = False
        # Took over the activity of a failed job, which is still in the island
        self.inherited = False


class IslandReporter(QObject):
    """Publishes every job of a `JobModel` as an activity, and acts on the island's buttons."""

    # The user pressed "Show" on a job's activity
    show_requested = Signal(object)

    def __init__(self, jobs, client, parent=None, interval_ms=_UPDATE_INTERVAL_MS):
        super().__init__(parent)
        self._jobs = jobs
        self._client = client
        self._interval = interval_ms / 1000
        self._activities = {}  # job id -> _Activity
        client.on_available = self._on_available
        client.on_unavailable = self._on_unavailable
        client.on_action = self._on_action
        client.on_dismissed = self._on_dismissed
        jobs.job_added.connect(self._on_job_added)
        jobs.job_changed.connect(self._on_job_changed)
        jobs.job_removed.connect(self._on_job_removed)

    # Jobs

    def _on_job_added(self, job):
        # A retry carries on under the activity of the job it replaces. It is
        # shown again, with Show, once it starts
        previous = self._jobs.predecessor(job)
        old = self._activities.pop(previous.id, None) if previous is not None else None
        if old is not None:
            self._stop_timer(old)
            activity = self._activities[job.id] = _Activity(old.id, job)
            activity.inherited = old.phase in (_Phase.SHOWING, _Phase.SHOWN)

    def _on_job_changed(self, job):
        activity = self._activities.get(job.id)
        if activity is not None and activity.inherited:
            # The failure it replaces is on show until this one says something
            if job.state is JobState.QUEUED:
                return
        elif activity is None or activity.phase is _Phase.UNSENT:
            if not self._worth_showing(job):
                return
            if activity is None:
                activity = self._activities[job.id] = _Activity(f"job-{job.id}", job)
        self._publish(activity)

    @staticmethod
    def _worth_showing(job):
        """Whether a job the island doesn't have should be put there now."""
        state = job.state
        if state is JobState.QUEUED:
            return False
        # Something that was over before it could be shown is only worth
        # the user's attention if it failed
        return not state.is_final or (state is JobState.FAILED and job.started)

    def _on_job_removed(self, job):
        activity = self._activities.pop(job.id, None)
        if activity is None:
            return
        self._stop_timer(activity)
        # A failure stays in the island until dismissed, so dismissing it here does that too.
        # Finished and cancelled activities leave the island by themselves
        if job.state is JobState.FAILED and activity.phase in (_Phase.SHOWING, _Phase.SHOWN):
            self._client.dismiss(activity.id)

    # Publishing

    def _publish(self, activity, now=False):
        """Bring the island up to date with the job, at most a few times a second."""
        if not self._client.available or activity.phase in (_Phase.SHOWING, _Phase.GONE):
            # While a Show is on its way, its reply publishes what changed since
            return
        job = activity.job
        if activity.phase is _Phase.SHOWN and not job.state.is_final and not now:
            # A final state is never held back; progress is
            wait = self._interval - (time.monotonic() - activity.last_sent)
            if wait > 0:
                self._later(activity, wait * 1000)
                return
        self._stop_timer(activity)
        fields = self._fields(job)
        activity.last_sent = time.monotonic()

        if activity.phase is _Phase.UNSENT:
            activity.phase = _Phase.SHOWING
            activity.sent = dict(fields)
            self._client.show(activity.id, fields, lambda code: self._on_shown(activity, code))
            return
        changed = {
            name: value for name, value in fields.items() if activity.sent.get(name) != value
        }
        if changed:
            activity.sent.update(changed)
            self._client.update(
                activity.id, changed, lambda code: self._on_updated(activity, changed, code)
            )

    def _fields(self, job):
        state = job.state
        fraction = job.progress.fraction
        fields = {"icon": job_model.ICONS[job.kind]}
        if state.is_final and fraction is not None:
            # Where the bar really ended up, not the last throttled value
            fields["progress"] = 1.0 if state is JobState.DONE else float(fraction)
        if state is JobState.DONE:
            fields |= {"state": "done", "title": job.summary, "text": ""}
        elif state is JobState.CANCELLED:
            # How far it got: "Cancelled after 5 of 12 files"
            fields |= {"state": "cancelled", "title": job_model.heading(job), "text": job.summary}
        elif state is JobState.FAILED:
            actions = [("retry", "Retry")] if job.can_retry else []
            fields |= {
                "state": "failed",
                "title": job.summary,
                "text": "",
                "error": job_model.error_text(job),
                "actions": [*actions, ("show", "Show")],
            }
        else:
            if state is JobState.PLANNING:
                text = "Preparing…"
            elif state is JobState.WAITING:
                text = "Waiting for you in Files"
            else:
                text = job_model.text(job, self._jobs.speed(job))
            fields |= {
                "state": "running",
                "title": job_model.heading(job),
                "text": text,
                # Negative is indeterminate. 1.0 is not "done": only the state says that
                "progress": -1.0 if fraction is None else float(fraction),
                "actions": [("cancel", "Cancel")],
            }
        return fields

    def _on_shown(self, activity, code):
        if activity.phase is not _Phase.SHOWING:
            return
        if code is None:
            activity.phase = _Phase.SHOWN
            activity.retries = 0
            # Whatever changed while the Show was on its way
            self._publish(activity, now=True)
            return
        activity.phase = _Phase.UNSENT
        activity.sent = {}
        if code == "LimitExceeded" and not activity.made_room:
            # Too many activities, most likely old failures still showing: drop
            # the oldest of ours from the island and try once more
            activity.made_room = True
            if self._dismiss_oldest_failure():
                self._publish(activity, now=True)
                return
        self._retry(activity)

    def _on_updated(self, activity, changed, code):
        if code is None:
            activity.retries = 0
            return
        # The island doesn't have these values after all
        for name in changed:
            activity.sent.pop(name, None)
        if code in ("NotFound", "InvalidState"):
            # Dismissed or timed out over there: stop updating it
            activity.phase = _Phase.GONE
            self._stop_timer(activity)
        elif activity.phase is _Phase.SHOWN:
            # RateLimited and the like: the next update carries newer values anyway
            self._retry(activity)

    def _retry(self, activity):
        """Try again in a while. A final state must get through; progress need not."""
        if activity.retries >= _MAX_RETRIES:
            return
        activity.retries += 1
        self._later(activity, _RETRY_MS, replace=True)

    def _dismiss_oldest_failure(self):
        for activity in self._activities.values():
            if activity.job.state is JobState.FAILED and activity.phase is _Phase.SHOWN:
                activity.phase = _Phase.GONE
                self._client.dismiss(activity.id)
                return True
        return False

    def _later(self, activity, msecs, replace=False):
        if activity.timer is not None:
            if not replace:
                return
            self._stop_timer(activity)
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self._on_timer(activity))
        activity.timer = timer
        timer.start(max(1, round(msecs)))

    def _on_timer(self, activity):
        self._stop_timer(activity)
        if self._activities.get(activity.job.id) is activity:
            self._publish(activity, now=True)

    @staticmethod
    def _stop_timer(activity):
        if activity.timer is not None:
            activity.timer.stop()
            activity.timer.deleteLater()
            activity.timer = None

    # The island

    def _on_available(self):
        """The island is there: started after this app, or restarted. It knows nothing of ours."""
        for job in self._jobs.jobs:
            if job.state.is_final:
                continue
            self._on_job_changed(job)

    def _on_unavailable(self):
        for activity in self._activities.values():
            self._stop_timer(activity)
            activity.phase = _Phase.UNSENT
            activity.sent = {}
            activity.retries = 0
            activity.made_room = False
            activity.inherited = False

    def _activity_with_id(self, activity_id):
        # Signals go to the whole bus and ids are only unique per sender,
        # so most of what arrives here is not ours
        return next((a for a in self._activities.values() if a.id == activity_id), None)

    def _on_action(self, activity_id, action):
        activity = self._activity_with_id(activity_id)
        if activity is None:
            return
        job = activity.job
        if action == "cancel":
            # A request. The activity stays running until the job says how it ended
            if not job.state.is_final:
                self._jobs.cancel(job)
        elif action == "retry":
            if job.can_retry:
                self._jobs.retry(job)
        elif action == "show":
            self.show_requested.emit(job)

    def _on_dismissed(self, activity_id):
        activity = self._activity_with_id(activity_id)
        if activity is None:
            return
        activity.phase = _Phase.GONE
        self._stop_timer(activity)
        if activity.job.state.is_final:
            # Keep the app's own panel in step
            self._jobs.dismiss(activity.job)
