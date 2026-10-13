"""One process for the session, and FileManager1, on a private bus: the real session
bus, and whatever file manager owns its names, are never touched."""

import shutil
import subprocess
import threading

import pytest
from gi.repository import Gio, GLib

from filehouse.integration import instance as instance_module
from filehouse.integration.instance import (
    BUS_NAME,
    FILE_MANAGER_INTERFACE,
    FILE_MANAGER_NAME,
    FILE_MANAGER_PATH,
    Instance,
    activation_token,
)

TIMEOUT = 5000


@pytest.fixture
def bus_address():
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
        address, flags.AUTHENTICATION_CLIENT | flags.MESSAGE_BUS_CONNECTION, None, None
    )


def owner(connection, name):
    try:
        reply = connection.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "GetNameOwner",
            GLib.Variant("(s)", (name,)),
            None,
            Gio.DBusCallFlags.NONE,
            TIMEOUT,
            None,
        )
    except GLib.Error:
        return None
    return reply.unpack()[0]


class Calls:
    """What the one process was asked, in order."""

    def __init__(self, instance):
        self.log = []
        for name in ("open", "show_folders", "show_items", "show_properties"):
            setattr(
                instance,
                f"on_{name}",
                lambda uris, token, name=name: self.log.append((name, uris, token)),
            )


def from_another_process(qtbot, call):
    """Run a blocking call as another process would make it: on a thread, while this
    one answers from its main loop."""
    result = []
    thread = threading.Thread(target=lambda: result.append(call()))
    thread.start()
    qtbot.waitUntil(lambda: not thread.is_alive(), timeout=TIMEOUT)
    thread.join()
    return result[0]


@pytest.fixture
def first(bus_address):
    one = Instance(connect(bus_address))
    assert one.claim() is True
    yield one
    one.close()


def test_the_first_process_is_the_one_and_the_file_manager(first, bus_address):
    watcher = connect(bus_address)

    assert first.primary and first.file_manager
    assert owner(watcher, BUS_NAME) == first._connection.get_unique_name()
    assert owner(watcher, FILE_MANAGER_NAME) == first._connection.get_unique_name()


def test_a_second_process_is_told_there_is_one(first, bus_address):
    second = Instance(connect(bus_address))

    assert second.claim() is False
    assert not second.primary and not second.file_manager
    # And it is not in the way of the first
    assert owner(connect(bus_address), BUS_NAME) == first._connection.get_unique_name()


def test_a_second_process_hands_over_what_it_was_started_with(qtbot, first, bus_address):
    calls = Calls(first)
    second = Instance(connect(bus_address))
    second.claim()
    uris = ["file:///home/me/Downloads", "file:///tmp/a%20b.txt"]

    assert from_another_process(qtbot, lambda: second.send(uris, "token-1")) is True

    assert calls.log == [("open", uris, "token-1")]


def test_started_with_nothing_it_still_asks_for_a_window(qtbot, first, bus_address):
    calls = Calls(first)
    second = Instance(connect(bus_address))
    second.claim()

    from_another_process(qtbot, lambda: second.send([], ""))

    assert calls.log == [("open", [], "")]


def test_a_later_start_can_ask_the_one_that_runs_to_end(qtbot, first, bus_address):
    calls = Calls(first)
    asked = []
    first.on_quit = lambda: asked.append(True)
    second = Instance(connect(bus_address))
    second.claim()

    assert from_another_process(qtbot, second.ask_to_quit) is True

    assert asked == [True]
    assert calls.log == []


def test_asking_one_that_is_gone_to_end_says_so(bus_address):
    first = Instance(connect(bus_address))
    first.claim()
    second = Instance(connect(bus_address))
    second.claim()
    first.close()

    assert second.ask_to_quit() is False


@pytest.mark.parametrize(
    "method, handler",
    [
        ("ShowFolders", "show_folders"),
        ("ShowItems", "show_items"),
        ("ShowItemProperties", "show_properties"),
    ],
)
def test_other_apps_ask_through_file_manager1(qtbot, first, bus_address, method, handler):
    calls = Calls(first)
    other = connect(bus_address)
    uris = ["file:///home/me/report.pdf"]

    def ask():
        other.call_sync(
            FILE_MANAGER_NAME,
            FILE_MANAGER_PATH,
            FILE_MANAGER_INTERFACE,
            method,
            GLib.Variant("(ass)", (uris, "startup-7")),
            None,
            Gio.DBusCallFlags.NONE,
            TIMEOUT,
            None,
        )
        return True

    assert from_another_process(qtbot, ask)
    assert calls.log == [(handler, uris, "startup-7")]


def test_a_handler_that_fails_does_not_fail_the_caller(qtbot, first, bus_address):
    def broken(uris, token):
        raise RuntimeError("no window to be had")

    first.on_open = broken
    second = Instance(connect(bus_address))
    second.claim()

    assert from_another_process(qtbot, lambda: second.send(["file:///x"], "")) is True


def test_when_another_file_manager_is_there_it_stays_the_file_manager(bus_address):
    dolphin = connect(bus_address)
    Gio.bus_own_name_on_connection(
        dolphin, FILE_MANAGER_NAME, Gio.BusNameOwnerFlags.NONE, None, None
    )
    assert owner(connect(bus_address), FILE_MANAGER_NAME) == dolphin.get_unique_name()

    one = Instance(connect(bus_address))

    assert one.claim() is True
    assert one.primary and not one.file_manager
    assert owner(connect(bus_address), FILE_MANAGER_NAME) == dolphin.get_unique_name()
    one.close()


def test_closing_gives_the_names_back_and_the_next_start_is_the_one(first, bus_address):
    first.close()

    assert owner(connect(bus_address), BUS_NAME) is None
    assert owner(connect(bus_address), FILE_MANAGER_NAME) is None
    again = Instance(connect(bus_address))
    assert again.claim() is True
    again.close()
    first.close()


def test_the_one_that_does_not_answer_is_said_to_be_gone(bus_address, monkeypatch):
    # Owns the name and never looks at its calls: a process that hangs
    stuck = connect(bus_address)
    Gio.bus_own_name_on_connection(stuck, BUS_NAME, Gio.BusNameOwnerFlags.NONE, None, None)
    monkeypatch.setattr(instance_module, "_SEND_TIMEOUT_MS", 200)
    second = Instance(connect(bus_address))
    assert second.claim() is False

    assert second.send(["file:///x"], "") is False


def test_without_a_session_bus_there_is_nothing_to_tell(monkeypatch):
    def no_bus(*_args):
        raise GLib.Error("no bus")

    monkeypatch.setattr(Gio, "bus_get_sync", no_bus)
    one = Instance()

    assert one.claim() is None
    assert not one.primary
    one.close()


def test_the_token_is_the_launchers(monkeypatch):
    monkeypatch.delenv("XDG_ACTIVATION_TOKEN", raising=False)
    monkeypatch.delenv("DESKTOP_STARTUP_ID", raising=False)
    assert activation_token() == ""
    monkeypatch.setenv("DESKTOP_STARTUP_ID", "x11-id")
    assert activation_token() == "x11-id"
    monkeypatch.setenv("XDG_ACTIVATION_TOKEN", "wayland-token")
    assert activation_token() == "wayland-token"
