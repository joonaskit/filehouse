"""The notification reporter against stand-ins for jobs, and the notifier against a
stand-in notification service on a private bus."""

import shutil
import subprocess

import pytest
from gi.repository import Gio, GLib
from PySide6.QtCore import QObject, Signal

from filehouse import APP_ID
from filehouse.core.errors import OperationError
from filehouse.core.operations import JobKind, JobState
from filehouse.integration import notifications
from filehouse.integration.notifications import (
    BUS_NAME,
    INTERFACE,
    OBJECT_PATH,
    NotificationReporter,
    Notifier,
)

TIMEOUT = 5000


class FakeJob:
    _ids = iter(range(1, 10_000))

    def __init__(self, kind=JobKind.COPY):
        self.id = next(self._ids)
        self.kind = kind
        self.sources = [Gio.File.new_for_path("/src/a.txt"), Gio.File.new_for_path("/src/b.txt")]
        self.destination = Gio.File.new_for_path("/home/me/Pictures")
        self.state = JobState.QUEUED
        self.summary = ""
        self.errors = []
        self.started = True
        self.is_undo = False
        self.acted = 0

    @property
    def error(self):
        return self.errors[0] if self.errors else None


class FakeJobs(QObject):
    job_changed = Signal(object)
    job_removed = Signal(object)

    def change(self, job, state):
        job.state = state
        self.job_changed.emit(job)


class FakeNotifier:
    def __init__(self):
        self.sent = []  # (summary, body, icon, urgent)

    def notify(self, summary, body, icon, urgent=False, done=None):
        self.sent.append((summary, body, icon, urgent))


class Setup:
    def __init__(self):
        self.jobs = FakeJobs()
        self.notifier = FakeNotifier()
        self.visible = False
        self.island = False
        self.now = 100.0
        self.reporter = NotificationReporter(
            self.jobs,
            self.notifier,
            lambda: self.visible,
            lambda: self.island,
            long_job_seconds=10,
            clock=lambda: self.now,
        )

    def run(self, job, seconds=1.0):
        self.jobs.change(job, JobState.RUNNING)
        self.now += seconds

    def end(self, job, state, summary="Copied 2 items to Pictures"):
        job.summary = summary
        self.jobs.change(job, state)


@pytest.fixture
def setup(qapp):
    return Setup()


def failed_job(setup, errors=1):
    job = FakeJob()
    job.errors = [
        OperationError(f"/src/file{index}.txt", "copy", "No space left") for index in range(errors)
    ]
    setup.run(job)
    setup.end(job, JobState.FAILED, "Could not copy “file0.txt”")
    return job


def test_a_finished_job_is_told_when_no_window_is_visible(setup):
    job = FakeJob()
    setup.run(job)
    setup.end(job, JobState.DONE)

    assert setup.notifier.sent == [("Copied 2 items to Pictures", "", "edit-copy", False)]


def test_nothing_is_sent_while_a_window_is_visible(setup):
    setup.visible = True
    job = FakeJob()
    setup.run(job)
    setup.end(job, JobState.DONE)
    failed_job(setup)

    assert setup.notifier.sent == []


def test_a_failure_is_urgent_and_names_the_first_error_and_how_many_more(setup):
    failed_job(setup, errors=3)

    ((summary, body, icon, urgent),) = setup.notifier.sent
    assert summary == "Could not copy “file0.txt”"
    assert "No space left" in body and "file0.txt" in body
    assert body.endswith(", and 2 more")
    assert icon == "dialog-error"
    assert urgent


def test_a_cancelled_job_is_not_told(setup):
    job = FakeJob()
    setup.run(job)
    setup.end(job, JobState.CANCELLED, "Cancelled after 1 of 2 files")

    assert setup.notifier.sent == []


def test_a_job_that_never_ran_is_not_told(setup):
    job = FakeJob()
    job.started = False
    setup.end(job, JobState.FAILED)

    assert setup.notifier.sent == []


def test_with_the_island_a_quick_job_is_not_told(setup):
    setup.island = True
    job = FakeJob()
    setup.run(job, seconds=3)
    setup.end(job, JobState.DONE)

    assert setup.notifier.sent == []


def test_with_the_island_a_long_job_is_told(setup):
    setup.island = True
    job = FakeJob()
    setup.run(job, seconds=45)
    setup.end(job, JobState.DONE)

    assert [sent[0] for sent in setup.notifier.sent] == ["Copied 2 items to Pictures"]


def test_with_the_island_a_failure_is_left_to_it(setup):
    setup.island = True
    job = FakeJob()
    setup.run(job, seconds=45)
    job.errors = [OperationError("/src/a.txt", "copy", "No space left")]
    setup.end(job, JobState.FAILED, "Could not copy “a.txt”")

    assert setup.notifier.sent == []


def test_a_job_is_told_once(setup):
    job = FakeJob()
    setup.run(job)
    setup.end(job, JobState.DONE)
    setup.jobs.job_changed.emit(job)

    assert len(setup.notifier.sent) == 1


def test_the_time_a_job_waited_in_the_queue_does_not_count(setup):
    setup.island = True
    job = FakeJob()
    setup.jobs.change(job, JobState.QUEUED)
    setup.now += 60
    setup.run(job, seconds=2)
    setup.end(job, JobState.DONE)

    assert setup.notifier.sent == []


# The notifier, on a bus of its own

_XML = f"""
<node>
  <interface name="{INTERFACE}">
    <method name="Notify">
      <arg type="s" direction="in"/><arg type="u" direction="in"/>
      <arg type="s" direction="in"/><arg type="s" direction="in"/>
      <arg type="s" direction="in"/><arg type="as" direction="in"/>
      <arg type="a{{sv}}" direction="in"/><arg type="i" direction="in"/>
      <arg type="u" direction="out"/>
    </method>
  </interface>
</node>
"""


@pytest.fixture
def bus_address():
    """A session bus of our own: the real notification service is never touched."""
    if shutil.which("dbus-daemon") is None:
        pytest.skip("dbus-daemon is not installed")
    daemon = subprocess.Popen(
        ["dbus-daemon", "--session", "--nofork", "--print-address=1"],
        stdout=subprocess.PIPE,
        text=True,
    )
    yield daemon.stdout.readline().strip()
    daemon.terminate()
    daemon.wait(5)


def connect(address):
    flags = Gio.DBusConnectionFlags
    return Gio.DBusConnection.new_for_address_sync(
        address, flags.AUTHENTICATION_CLIENT | flags.MESSAGE_BUS_CONNECTION, None, None
    )


class FakeService:
    def __init__(self, address):
        self.connection = connect(address)
        self.calls = []
        node = Gio.DBusNodeInfo.new_for_xml(_XML)
        self.connection.register_object(OBJECT_PATH, node.interfaces[0], self._on_call, None, None)
        Gio.bus_own_name_on_connection(self.connection, BUS_NAME, Gio.BusNameOwnerFlags.NONE)

    def _on_call(self, _connection, _sender, _path, _interface, _method, parameters, invocation):
        self.calls.append(parameters.unpack())
        invocation.return_value(GLib.Variant("(u)", (len(self.calls),)))


def test_the_notifier_sends_what_the_service_expects(qtbot, bus_address):
    service = FakeService(bus_address)
    notifier = Notifier(connect(bus_address))
    answers = []

    notifier.notify("Copied 4 items to Pictures", "", "edit-copy", done=answers.append)
    qtbot.waitUntil(lambda: bool(answers), timeout=TIMEOUT)

    ((app, replaces, icon, summary, body, actions, hints, timeout),) = service.calls
    assert (app, replaces, icon, summary, body, actions) == (
        APP_ID,
        0,
        "edit-copy",
        "Copied 4 items to Pictures",
        "",
        [],
    )
    assert hints == {"urgency": notifications.URGENCY_NORMAL, "desktop-entry": APP_ID}
    assert timeout == -1
    assert answers == [True]


def test_a_failure_is_critical_and_does_not_expire(qtbot, bus_address):
    service = FakeService(bus_address)
    notifier = Notifier(connect(bus_address))
    answers = []

    notifier.notify("Could not copy", "No space left", "dialog-error", True, answers.append)
    qtbot.waitUntil(lambda: bool(answers), timeout=TIMEOUT)

    *_, hints, timeout = service.calls[0]
    assert hints["urgency"] == notifications.URGENCY_CRITICAL
    assert timeout == 0


def test_without_a_service_it_is_silent(qtbot, bus_address):
    notifier = Notifier(connect(bus_address))
    answers = []

    notifier.notify("Copied", "", "edit-copy", done=answers.append)
    qtbot.waitUntil(lambda: bool(answers), timeout=TIMEOUT)

    assert answers == [False]


def test_without_a_bus_it_is_silent():
    answers = []

    Notifier().notify("Copied", "", "edit-copy", done=answers.append)
    Notifier().flush()

    assert answers == [False]


def test_nothing_is_sent_when_the_user_turned_notifications_off(setup):
    setup.reporter._enabled = lambda: False
    failed_job(setup)

    assert setup.notifier.sent == []


def test_the_threshold_can_be_a_function(setup):
    setup.island = True
    setup.reporter._long = lambda: 5
    job = FakeJob()
    setup.run(job, seconds=6)
    setup.end(job, JobState.DONE)

    assert len(setup.notifier.sent) == 1


def rules_job(acted=2, errors=()):
    job = FakeJob(JobKind.RULES)
    job.destination = Gio.File.new_for_path("/home/me/Downloads")
    job.acted = acted
    job.errors = list(errors)
    return job


def test_a_run_of_rules_is_told_even_with_the_island_and_quickly(setup):
    setup.island = True
    job = rules_job()
    setup.run(job, seconds=0.1)
    setup.end(job, JobState.DONE, "Rules acted on 2 files")

    assert setup.notifier.sent == [("Rules acted on 2 files", "", "view-filter", False)]


def test_a_run_of_rules_that_did_nothing_is_not_told(setup):
    job = rules_job(acted=0)
    setup.run(job)
    setup.end(job, JobState.DONE, "Rules acted on 0 files")

    assert setup.notifier.sent == []


def test_a_run_of_rules_is_not_told_while_a_window_is_visible(setup):
    setup.visible = True
    job = rules_job()
    setup.run(job)
    setup.end(job, JobState.DONE, "Rules acted on 2 files")

    assert setup.notifier.sent == []


def test_the_same_failure_of_rules_is_told_once(setup):
    setup.island = True
    error = OperationError("/home/me/Downloads/a.pdf", "move", "Permission denied")
    other = OperationError("/home/me/Downloads/b.pdf", "move", "Permission denied")

    for errors in ([error], [error], [error, other]):
        job = rules_job(acted=0, errors=errors)
        setup.run(job)
        setup.end(job, JobState.FAILED, "Rules acted on 0 of 1 file, 1 failed")

    assert len(setup.notifier.sent) == 2
    assert all(urgent for *_rest, urgent in setup.notifier.sent)
