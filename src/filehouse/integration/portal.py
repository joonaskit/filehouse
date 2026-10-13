"""The Open and Save dialog of other apps: a backend of the desktop's file chooser portal.

Apps that use the portal (every Flatpak, and Qt and GTK apps set up for it) ask
`org.freedesktop.portal.FileChooser` for a file. The portal hands the question to a
backend, `org.freedesktop.impl.portal.FileChooser`, and this is one: the process owns
`org.freedesktop.impl.portal.desktop.filehouse` and answers `OpenFile`, `SaveFile` and
`SaveFiles`. Whether the portal asks this backend is the user's choice, made in
`portals.conf` (see `install.sh`).

Each call becomes a `Request`, given to `on_request`, which shows a window and in the
end calls `respond` or `cancel` on it. A call is always answered: one that nothing takes,
or that fails on the way to a window, is answered as failed, since an app's Open would
otherwise wait for ever.

No Qt here, and nothing blocks: the name is asked for asynchronously, and without a
session bus, or with the name taken, there is simply no backend.
"""

import logging
import os
from dataclasses import dataclass

from gi.repository import Gio, GLib

from filehouse.core.filters import Filter

log = logging.getLogger(__name__)

BUS_NAME = "org.freedesktop.impl.portal.desktop.filehouse"
OBJECT_PATH = "/org/freedesktop/portal/desktop"
INTERFACE = "org.freedesktop.impl.portal.FileChooser"
REQUEST_INTERFACE = "org.freedesktop.impl.portal.Request"

# What a call asks for
OPEN, SAVE, SAVE_FILES = "OpenFile", "SaveFile", "SaveFiles"
# The portal's answers: chosen, the user said no, and it ended some other way
SUCCESS, CANCELLED, ENDED = 0, 1, 2

_CALL = """
      <arg type="o" name="handle" direction="in"/>
      <arg type="s" name="app_id" direction="in"/>
      <arg type="s" name="parent_window" direction="in"/>
      <arg type="s" name="title" direction="in"/>
      <arg type="a{sv}" name="options" direction="in"/>
      <arg type="u" name="response" direction="out"/>
      <arg type="a{sv}" name="results" direction="out"/>
"""

_XML = f"""
<node>
  <interface name="{INTERFACE}">
    <method name="{OPEN}">{_CALL}</method>
    <method name="{SAVE}">{_CALL}</method>
    <method name="{SAVE_FILES}">{_CALL}</method>
  </interface>
  <interface name="{REQUEST_INTERFACE}">
    <method name="Close"/>
  </interface>
</node>
"""

_BUS_TIMEOUT_MS = 2000
_DO_NOT_QUEUE = 4
_PRIMARY_OWNER, _ALREADY_OWNER = 1, 4


@dataclass(frozen=True)
class Choice:
    """One more thing the app asks beside the file ("Encoding"): a few options to pick
    from, or, with no options, a yes or no whose values are "true" and "false"."""

    id: str
    label: str
    options: tuple  # of (id, label)
    default: str


class Request:
    """What an app asked, and the way to answer it. The window sets `on_closed`, which is
    called when the app took the question back: the window closes, nothing is answered."""

    def __init__(self, kind, app_id="", parent_window="", title="", answer=None):
        self.kind = kind
        self.app_id = app_id
        # "wayland:<handle>" or "x11:<id>" of the window that asked, or empty
        self.parent_window = parent_window
        self.title = title
        self.accept_label = ""
        self.modal = True
        # OpenFile: several files, or folders instead of files
        self.multiple = False
        self.directory = False
        self.filters = []
        self.current_filter = None
        self.choices = []
        # Where to start: a folder, and for SaveFile maybe the file being saved again
        self.folder = None
        self.file = None
        # SaveFile: the name to suggest. SaveFiles: the names to be saved in one folder
        self.name = ""
        self.names = []
        self.on_closed = lambda: None
        self._answer = answer

    @property
    def answered(self):
        return self._answer is None

    def respond(self, locations, choices=None, chosen_filter=None):
        """The user chose. `locations` are files of this computer: the portal takes no
        others. `choices` is {id of a Choice: id of the option}."""
        results = {"uris": GLib.Variant("as", [location.get_uri() for location in locations])}
        if choices:
            results["choices"] = GLib.Variant("a(ss)", sorted(choices.items()))
        if chosen_filter is not None:
            results["current_filter"] = GLib.Variant(
                "(sa(us))", (chosen_filter.name, list(chosen_filter.patterns))
            )
        self._finish(SUCCESS, results)

    def cancel(self):
        self._finish(CANCELLED, {})

    def fail(self):
        self._finish(ENDED, {})

    def _finish(self, response, results):
        answer, self._answer = self._answer, None
        if answer is not None:
            answer(response, results)


def accept_text(label):
    """A button's label as the portal gives it, with "_" before the letter that is its
    key, as Qt wants it: with "&"."""
    text = ""
    escaped = False
    for character in label:
        if character == "_" and not escaped:
            escaped = True
            continue
        if escaped and character != "_":
            text += "&"
        text += "&&" if character == "&" else character
        escaped = False
    return text


def _path(value):
    """A file from a path as the portal sends one: bytes with a zero at the end."""
    path = bytes(value).split(b"\0", 1)[0]
    return Gio.File.new_for_path(os.fsdecode(path)) if path else None


def _filter(value):
    name, patterns = value
    return Filter(str(name), tuple((int(kind), str(text)) for kind, text in patterns))


def _choice(value):
    identifier, label, options, default = value
    return Choice(
        str(identifier), str(label), tuple((str(a), str(b)) for a, b in options), str(default)
    )


# How each option is read. One that is not as it should be is left out, not the call
_OPTIONS = {
    "accept_label": ("accept_label", str),
    "modal": ("modal", bool),
    "multiple": ("multiple", bool),
    "directory": ("directory", bool),
    "filters": ("filters", lambda value: [_filter(item) for item in value]),
    "current_filter": ("current_filter", _filter),
    "choices": ("choices", lambda value: [_choice(item) for item in value]),
    "current_name": ("name", str),
    "current_folder": ("folder", _path),
    "current_file": ("file", _path),
    "files": (
        "names",
        lambda value: [os.fsdecode(bytes(item).split(b"\0", 1)[0]) for item in value],
    ),
}


def read_options(request, options):
    for key, value in options.items():
        if key not in _OPTIONS:
            continue
        attribute, read = _OPTIONS[key]
        try:
            setattr(request, attribute, read(value))
        except (TypeError, ValueError):
            log.debug("the option %s of a file dialog is not as expected", key)
    request.names = [name for name in request.names if name and "/" not in name]
    if request.current_filter is not None and request.current_filter not in request.filters:
        request.filters.insert(0, request.current_filter)


class FileChooserPortal:
    """Set `on_request`, then call `start()`. Requests arrive on the thread of the main
    context. Create it on the main thread."""

    def __init__(self, connection):
        self._connection = connection
        self._node = Gio.DBusNodeInfo.new_for_xml(_XML)
        self._registration = None
        # The questions that are open: object path -> (Request, its registration)
        self._open = {}
        self.active = False
        self.on_request = None

    def start(self):
        if self._connection is None:
            return
        # Registered first: a call may come the moment the name is ours
        try:
            self._registration = self._register(OBJECT_PATH, 0)
        except GLib.Error as error:
            log.debug("no file dialogs for other apps: %s", error.message)
            return
        self._bus_call(
            "RequestName", GLib.Variant("(su)", (BUS_NAME, _DO_NOT_QUEUE)), self._on_name
        )

    def _on_name(self, connection, result):
        try:
            answer = connection.call_finish(result).unpack()[0]
        except GLib.Error as error:
            log.debug("could not ask for %s: %s", BUS_NAME, error.message)
            return
        self.active = answer in (_PRIMARY_OWNER, _ALREADY_OWNER)

    def close(self):
        """Give the name back, and answer what is still open. Safe to call more than once."""
        if self._connection is None or self._registration is None:
            return
        for request, _registration in list(self._open.values()):
            request.fail()
        self._connection.unregister_object(self._registration)
        self._registration = None
        self._bus_call("ReleaseName", GLib.Variant("(s)", (BUS_NAME,)), None)
        self.active = False
        self.flush()

    def flush(self):
        """Before the process ends: the answer to an app must be on the bus by then."""
        if self._connection is not None:
            try:
                self._connection.flush_sync(None)
            except GLib.Error:
                pass

    def _register(self, path, interface):
        register = getattr(
            self._connection, "register_object_with_closures2", self._connection.register_object
        )
        return register(path, self._node.interfaces[interface], self._on_call, None, None)

    def _bus_call(self, method, parameters, done):
        self._connection.call(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            method,
            parameters,
            None,
            Gio.DBusCallFlags.NONE,
            _BUS_TIMEOUT_MS,
            None,
            done,
        )

    def _on_call(self, _connection, _sender, path, interface, method, parameters, invocation):
        if interface == REQUEST_INTERFACE:
            invocation.return_value(None)
            self._on_withdrawn(path)
            return
        handle, app_id, parent_window, title, options = parameters.unpack()

        def answer(response, results):
            self._forget(handle)
            invocation.return_value(GLib.Variant("(ua{sv})", (response, results)))

        request = Request(method, app_id, parent_window, title, answer)
        try:
            read_options(request, options)
            # The portal ends the question through this object when the app goes away
            self._open[handle] = (request, self._register(handle, 1))
            if self.on_request is None:
                raise RuntimeError("nothing shows file dialogs")
            self.on_request(request)
        except Exception:
            log.exception("%s failed", method)
            request.fail()

    def _on_withdrawn(self, path):
        request = self._open.get(path, (None, None))[0]
        if request is None:
            return
        try:
            request.on_closed()
        except Exception:
            log.exception("closing a file dialog failed")
        request.fail()

    def _forget(self, path):
        entry = self._open.pop(path, None)
        if entry is not None:
            self._connection.unregister_object(entry[1])
