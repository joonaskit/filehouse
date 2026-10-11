"""The D-Bus client against a stand-in activities service on a private bus."""

import shutil
import subprocess

import pytest
from gi.repository import Gio, GLib

from filehouse.integration.island import BUS_NAME, INTERFACE, OBJECT_PATH, IslandClient

TIMEOUT = 5000

_XML = f"""
<node>
  <interface name="{INTERFACE}">
    <method name="Show">
      <arg type="s" direction="in"/><arg type="a{{sv}}" direction="in"/>
    </method>
    <method name="Update">
      <arg type="s" direction="in"/><arg type="a{{sv}}" direction="in"/>
    </method>
    <method name="Dismiss"><arg type="s" direction="in"/></method>
    <signal name="ActionInvoked"><arg type="s"/><arg type="s"/></signal>
    <signal name="Dismissed"><arg type="s"/></signal>
    <property name="Version" type="u" access="read"/>
  </interface>
</node>
"""


@pytest.fixture
def bus_address():
    """A session bus of our own: the real one, and a real island on it, are never touched."""
    if shutil.which("dbus-daemon") is None:
        pytest.skip("dbus-daemon is not installed")
    daemon = subprocess.Popen(
        ["dbus-daemon", "--session", "--nofork", "--print-address=1"],
        stdout=subprocess.PIPE,
        text=True,
    )
    address = daemon.stdout.readline().strip()
    yield address
    daemon.terminate()
    daemon.wait(5)


def connect(address):
    flags = Gio.DBusConnectionFlags
    return Gio.DBusConnection.new_for_address_sync(
        address,
        flags.AUTHENTICATION_CLIENT | flags.MESSAGE_BUS_CONNECTION,
        None,
        None,
    )


class FakeService:
    """Owns the island's bus name and records what it is asked."""

    def __init__(self, address, version=1):
        self.connection = connect(address)
        self.version = version
        self.calls = []  # (method, unpacked arguments, signature of the arguments)
        self.errors = []  # error codes to answer the next calls with
        self.silent = False  # take calls and never reply
        node = Gio.DBusNodeInfo.new_for_xml(_XML)
        self.connection.register_object(
            OBJECT_PATH, node.interfaces[0], self._on_call, self._on_get, None
        )
        self.owner = 0
        self.appear()

    def appear(self):
        self.owner = Gio.bus_own_name_on_connection(
            self.connection, BUS_NAME, Gio.BusNameOwnerFlags.NONE, None, None
        )

    def vanish(self):
        Gio.bus_unown_name(self.owner)

    def emit(self, signal, *arguments):
        signature = "(" + "s" * len(arguments) + ")"
        self.connection.emit_signal(
            None, OBJECT_PATH, INTERFACE, signal, GLib.Variant(signature, arguments)
        )

    def _on_call(self, _connection, _sender, _path, _interface, method, parameters, invocation):
        self.calls.append((method, parameters.unpack(), parameters.get_type_string()))
        if self.silent:
            return
        if self.errors:
            code = self.errors.pop(0)
            invocation.return_dbus_error(f"{BUS_NAME}.Error.{code}", "refused")
        else:
            invocation.return_value(None)

    def _on_get(self, _connection, _sender, _path, _interface, name):
        return GLib.Variant("u", self.version) if name == "Version" else None


class Events:
    def __init__(self, client):
        self.log = []
        client.on_available = lambda: self.log.append("available")
        client.on_unavailable = lambda: self.log.append("unavailable")
        client.on_action = lambda activity_id, action: self.log.append((activity_id, action))
        client.on_dismissed = lambda activity_id: self.log.append(("dismissed", activity_id))


@pytest.fixture
def client(qtbot, bus_address):
    client = IslandClient(connect(bus_address))
    client.events = Events(client)
    yield client
    client.close()


def test_available_when_the_island_is_there_with_the_right_version(qtbot, bus_address, client):
    FakeService(bus_address)

    client.start()

    qtbot.waitUntil(lambda: client.available, timeout=TIMEOUT)
    assert client.events.log == ["available"]


def test_silent_without_an_island(qtbot, bus_address, client):
    client.start()
    qtbot.wait(200)
    results = []

    client.show("job-1", {"title": "Copying"}, results.append)
    client.dismiss("job-1")
    qtbot.wait(200)

    assert not client.available
    assert client.events.log == []
    assert results == []


def test_silent_with_another_api_version(qtbot, bus_address, client):
    service = FakeService(bus_address, version=2)

    client.start()
    qtbot.wait(300)
    client.show("job-1", {"title": "Copying"})
    qtbot.wait(100)

    assert not client.available
    assert service.calls == []


def test_calls_carry_the_types_the_api_asks_for(qtbot, bus_address, client):
    service = FakeService(bus_address)
    client.start()
    qtbot.waitUntil(lambda: client.available, timeout=TIMEOUT)
    results = []

    client.show(
        "job-1",
        {
            "title": "Copying 12 files",
            "text": "Preparing…",
            "icon": "edit-copy",
            "progress": -1.0,
            "state": "running",
            "actions": [("cancel", "Cancel")],
        },
        results.append,
    )
    client.update("job-1", {"progress": 1, "error": "report.pdf: permission denied"})
    client.dismiss("job-1", results.append)
    qtbot.waitUntil(lambda: len(results) == 2, timeout=TIMEOUT)

    assert results == [None, None]
    assert [(method, signature) for method, _args, signature in service.calls] == [
        ("Show", "(sa{sv})"),
        ("Update", "(sa{sv})"),
        ("Dismiss", "(s)"),
    ]
    assert service.calls[0][1] == (
        "job-1",
        {
            "title": "Copying 12 files",
            "text": "Preparing…",
            "icon": "edit-copy",
            "progress": -1.0,
            "state": "running",
            "actions": [("cancel", "Cancel")],
        },
    )
    # An integer progress still goes out as a double
    assert service.calls[1][1] == (
        "job-1",
        {"progress": 1.0, "error": "report.pdf: permission denied"},
    )
    assert isinstance(service.calls[1][1][1]["progress"], float)


@pytest.mark.parametrize("code", ["NotFound", "LimitExceeded", "RateLimited", "InvalidArgs"])
def test_errors_come_back_as_their_code(qtbot, bus_address, client, code):
    service = FakeService(bus_address)
    service.errors = [code]
    client.start()
    qtbot.waitUntil(lambda: client.available, timeout=TIMEOUT)
    results = []

    client.update("job-1", {"progress": 0.5}, results.append)
    qtbot.waitUntil(lambda: bool(results), timeout=TIMEOUT)

    assert results == [code]


def test_actions_and_dismissals_arrive(qtbot, bus_address, client):
    service = FakeService(bus_address)
    client.start()
    qtbot.waitUntil(lambda: client.available, timeout=TIMEOUT)

    service.emit("ActionInvoked", "job-3", "cancel")
    service.emit("Dismissed", "job-4")

    qtbot.waitUntil(lambda: len(client.events.log) == 3, timeout=TIMEOUT)
    assert client.events.log[1:] == [("job-3", "cancel"), ("dismissed", "job-4")]


def test_follows_the_island_going_and_coming_back(qtbot, bus_address, client):
    service = FakeService(bus_address)
    client.start()
    qtbot.waitUntil(lambda: client.available, timeout=TIMEOUT)

    service.vanish()
    qtbot.waitUntil(lambda: not client.available, timeout=TIMEOUT)
    client.show("job-1", {"title": "Copying"})

    service.appear()
    qtbot.waitUntil(lambda: client.available, timeout=TIMEOUT)

    assert client.events.log == ["available", "unavailable", "available"]
    assert service.calls == []


def test_an_island_started_after_the_app_is_found(qtbot, bus_address, client):
    client.start()
    qtbot.wait(100)
    assert not client.available

    FakeService(bus_address)

    qtbot.waitUntil(lambda: client.available, timeout=TIMEOUT)


def test_an_island_that_never_answers_does_not_block(qtbot, bus_address, client, monkeypatch):
    from filehouse.integration import island

    monkeypatch.setattr(island, "_CALL_TIMEOUT_MS", 150)
    service = FakeService(bus_address)
    client.start()
    qtbot.waitUntil(lambda: client.available, timeout=TIMEOUT)
    # From now on the island takes every call and never replies
    service.silent = True
    results = []

    client.update("job-1", {"progress": 0.5}, results.append)
    # The call returned at once; the UI thread is free while the island is silent
    assert results == []

    qtbot.waitUntil(lambda: bool(results), timeout=TIMEOUT)
    assert results == ["Failed"]
