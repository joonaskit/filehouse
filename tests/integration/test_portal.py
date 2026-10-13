"""The file chooser backend on a private bus, asked as the portal would ask it: the
real session bus and the real portal are never touched."""

import shutil
import subprocess

import pytest
from gi.repository import Gio, GLib

from filehouse.core.filters import GLOB, MIME, Filter
from filehouse.integration import portal
from filehouse.integration.portal import Choice, FileChooserPortal, accept_text

TIMEOUT = 5000
HANDLE = "/org/freedesktop/portal/desktop/request/1_1/t"


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


@pytest.fixture
def backend(qtbot, bus_address):
    backend = FileChooserPortal(connect(bus_address))
    backend.requests = []
    backend.on_request = backend.requests.append
    backend.start()
    qtbot.waitUntil(lambda: backend.active, timeout=TIMEOUT)
    yield backend
    backend.close()


class Caller:
    """The portal's side: asks, and keeps the answers as they come."""

    def __init__(self, address):
        self.connection = connect(address)
        self.answers = []
        self.errors = []

    def ask(self, method, options=None, handle=HANDLE, title="Pick"):
        self.connection.call(
            portal.BUS_NAME,
            portal.OBJECT_PATH,
            portal.INTERFACE,
            method,
            GLib.Variant(
                "(osssa{sv})", (handle, "org.example.App", "x11:1a", title, options or {})
            ),
            GLib.VariantType("(ua{sv})"),
            Gio.DBusCallFlags.NONE,
            TIMEOUT,
            None,
            self._on_answer,
        )

    def _on_answer(self, connection, result):
        try:
            self.answers.append(connection.call_finish(result).unpack())
        except GLib.Error as error:
            self.errors.append(error.message)

    def withdraw(self, handle=HANDLE):
        self.connection.call(
            portal.BUS_NAME,
            handle,
            portal.REQUEST_INTERFACE,
            "Close",
            None,
            None,
            Gio.DBusCallFlags.NONE,
            TIMEOUT,
            None,
            None,
        )


@pytest.fixture
def caller(bus_address):
    return Caller(bus_address)


def path(text):
    return GLib.Variant("ay", text.encode() + b"\0")


def asked(qtbot, backend, count=1):
    qtbot.waitUntil(lambda: len(backend.requests) == count, timeout=TIMEOUT)
    return backend.requests[-1]


def answered(qtbot, caller, count=1):
    qtbot.waitUntil(lambda: len(caller.answers) + len(caller.errors) == count, timeout=TIMEOUT)
    assert not caller.errors
    return caller.answers[-1]


def test_a_call_becomes_a_request_with_what_was_asked(qtbot, backend, caller):
    images = ("Images", [(GLOB, "*.png"), (MIME, "image/jpeg")])
    caller.ask(
        "OpenFile",
        {
            "accept_label": GLib.Variant("s", "_Import"),
            "modal": GLib.Variant("b", False),
            "multiple": GLib.Variant("b", True),
            "filters": GLib.Variant("a(sa(us))", [images, ("All", [(GLOB, "*")])]),
            "current_filter": GLib.Variant("(sa(us))", images),
            "choices": GLib.Variant(
                "a(ssa(ss)s)",
                [
                    ("encoding", "Encoding", [("utf8", "UTF-8"), ("latin", "Latin 1")], "latin"),
                    ("again", "Open again", [], "true"),
                ],
            ),
            "current_folder": path("/tmp/somewhere"),
            "unknown": GLib.Variant("s", "ignored"),
        },
        title="Import pictures",
    )

    request = asked(qtbot, backend)

    assert request.kind == portal.OPEN
    assert (request.app_id, request.parent_window) == ("org.example.App", "x11:1a")
    assert (request.title, request.accept_label) == ("Import pictures", "_Import")
    assert (request.modal, request.multiple, request.directory) == (False, True, False)
    wanted = Filter("Images", ((GLOB, "*.png"), (MIME, "image/jpeg")))
    assert request.filters == [wanted, Filter("All", ((GLOB, "*"),))]
    assert request.current_filter == wanted
    assert request.choices == [
        Choice("encoding", "Encoding", (("utf8", "UTF-8"), ("latin", "Latin 1")), "latin"),
        Choice("again", "Open again", (), "true"),
    ]
    assert request.folder.get_path() == "/tmp/somewhere"
    request.cancel()


def test_saving_names_the_file_and_several_files_their_names(qtbot, backend, caller):
    caller.ask(
        "SaveFile",
        {"current_name": GLib.Variant("s", "a.txt"), "current_file": path("/tmp/old/a.txt")},
    )
    one = asked(qtbot, backend)
    caller.ask(
        "SaveFiles",
        {"files": GLib.Variant("aay", [b"one.txt\0", b"two.txt\0", b"../out.txt\0", b"\0"])},
        handle=HANDLE + "2",
    )
    several = asked(qtbot, backend, 2)

    assert (one.kind, one.name, one.file.get_path()) == (portal.SAVE, "a.txt", "/tmp/old/a.txt")
    assert one.modal and one.folder is None
    # A name that is a path is not a name
    assert (several.kind, several.names) == (portal.SAVE_FILES, ["one.txt", "two.txt"])
    one.cancel()
    several.cancel()


def test_a_filter_that_is_current_but_not_offered_is_offered(qtbot, backend, caller):
    caller.ask("OpenFile", {"current_filter": GLib.Variant("(sa(us))", ("Text", [(0, "*.txt")]))})

    request = asked(qtbot, backend)

    assert request.filters == [Filter("Text", ((GLOB, "*.txt"),))]
    request.cancel()


def test_the_answer_is_the_files_the_choices_and_the_filter(qtbot, backend, caller):
    caller.ask("OpenFile")
    request = asked(qtbot, backend)

    request.respond(
        [Gio.File.new_for_path("/tmp/a b.txt"), Gio.File.new_for_path("/tmp/c.txt")],
        {"encoding": "utf8"},
        Filter("Text", ((GLOB, "*.txt"),)),
    )

    assert answered(qtbot, caller) == (
        portal.SUCCESS,
        {
            "uris": ["file:///tmp/a%20b.txt", "file:///tmp/c.txt"],
            "choices": [("encoding", "utf8")],
            "current_filter": ("Text", [(0, "*.txt")]),
        },
    )
    assert request.answered


def test_a_closed_dialog_is_an_answer_too_and_only_the_first_answer_counts(qtbot, backend, caller):
    caller.ask("SaveFile")
    request = asked(qtbot, backend)

    request.cancel()
    request.respond([Gio.File.new_for_path("/tmp/late.txt")])

    assert answered(qtbot, caller) == (portal.CANCELLED, {})


def test_taken_back_by_the_app_the_dialog_is_told_to_close(qtbot, backend, caller):
    caller.ask("OpenFile")
    request = asked(qtbot, backend)
    closed = []
    request.on_closed = lambda: closed.append(True)

    caller.withdraw()

    assert answered(qtbot, caller) == (portal.ENDED, {})
    assert closed == [True]
    # The object of the question is gone with it: nothing answers there any more
    caller.withdraw()
    qtbot.wait(50)
    assert closed == [True]


def test_a_call_nothing_takes_is_answered_as_failed(qtbot, backend, caller):
    backend.on_request = None
    caller.ask("OpenFile")

    assert answered(qtbot, caller) == (portal.ENDED, {})


def test_a_dialog_that_fails_to_open_is_answered_as_failed(qtbot, backend, caller):
    def broken(_request):
        raise RuntimeError("no window")

    backend.on_request = broken
    caller.ask("OpenFile")

    assert answered(qtbot, caller) == (portal.ENDED, {})
    # And the next one is asked as if nothing happened, under the same handle
    backend.on_request = backend.requests.append
    caller.ask("OpenFile")
    asked(qtbot, backend).cancel()
    assert answered(qtbot, caller, 2) == (portal.CANCELLED, {})


def test_what_is_open_when_the_process_ends_is_answered(qtbot, backend, caller):
    caller.ask("OpenFile")
    asked(qtbot, backend)

    backend.close()

    assert answered(qtbot, caller) == (portal.ENDED, {})


def test_an_option_that_is_not_as_it_should_be_is_left_out(qtbot, backend, caller):
    caller.ask(
        "OpenFile",
        {"filters": GLib.Variant("s", "nonsense"), "multiple": GLib.Variant("b", True)},
    )

    request = asked(qtbot, backend)

    assert request.filters == [] and request.multiple
    request.cancel()


def test_with_the_name_taken_there_is_no_backend(qtbot, backend, bus_address):
    second = FileChooserPortal(connect(bus_address))
    second.start()
    qtbot.wait(100)

    assert backend.active and not second.active
    second.close()


def test_without_a_bus_nothing_happens():
    backend = FileChooserPortal(None)
    backend.start()
    backend.flush()
    backend.close()

    assert not backend.active


@pytest.mark.parametrize(
    "label, text",
    [("_Open", "&Open"), ("Save _as", "Save &as"), ("a__b", "a_b"), ("R&D", "R&&D"), ("", "")],
)
def test_the_key_of_a_button_is_marked_as_qt_marks_it(label, text):
    assert accept_text(label) == text
