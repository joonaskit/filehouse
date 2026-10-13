"""One process for the whole session, and the desktop's way to ask it for a folder.

The first process to start owns a name on the session bus. A later start finds the
name taken, hands its paths to the owner and ends, so there is one set of jobs, one
island client and one list of favourites however the app is started.

The same process serves `org.freedesktop.FileManager1`, which is how other apps ask
for "Show in folder": a browser for a download, an editor for the file it has open.

No Qt here, so it can be asked before there is an application. Without a session bus
nothing here does anything, and the app runs as a process of its own, as it used to.
"""

import logging
import os

from gi.repository import Gio, GLib

log = logging.getLogger(__name__)

BUS_NAME = "io.github.joonaskit.filehouse"
OBJECT_PATH = "/io/github/joonaskit/filehouse"
INTERFACE = "io.github.joonaskit.filehouse.Instance"

FILE_MANAGER_NAME = "org.freedesktop.FileManager1"
FILE_MANAGER_PATH = "/org/freedesktop/FileManager1"
FILE_MANAGER_INTERFACE = "org.freedesktop.FileManager1"

_XML = f"""
<node>
  <interface name="{INTERFACE}">
    <method name="Open">
      <arg type="as" name="uris" direction="in"/>
      <arg type="s" name="activation_token" direction="in"/>
    </method>
    <method name="Quit"/>
  </interface>
  <interface name="{FILE_MANAGER_INTERFACE}">
    <method name="ShowFolders">
      <arg type="as" name="uris" direction="in"/>
      <arg type="s" name="startup_id" direction="in"/>
    </method>
    <method name="ShowItems">
      <arg type="as" name="uris" direction="in"/>
      <arg type="s" name="startup_id" direction="in"/>
    </method>
    <method name="ShowItemProperties">
      <arg type="as" name="uris" direction="in"/>
      <arg type="s" name="startup_id" direction="in"/>
    </method>
  </interface>
</node>
"""

# The bus itself answers these at once; a bus that does not is as good as none
_BUS_TIMEOUT_MS = 2000
# The process that runs has to answer on its main thread, which may be busy
_SEND_TIMEOUT_MS = 5000
_DO_NOT_QUEUE = 4
# Answers of RequestName
_PRIMARY_OWNER, _ALREADY_OWNER = 1, 4


def activation_token():
    """What the launcher gave this process so its window may come to the front: the
    Wayland token, or the startup id of X11. Empty when it was started without one."""
    return os.environ.get("XDG_ACTIVATION_TOKEN") or os.environ.get("DESKTOP_STARTUP_ID") or ""


class Instance:
    """Set the `on_...` callbacks, each taking a list of URIs and a token, then call
    `claim()`. Calls from other processes arrive on the thread of the main context."""

    def __init__(self, connection=None):
        self._connection = connection
        self._registrations = []
        self.primary = False
        # Whether this process is the session's file manager for other apps
        self.file_manager = False
        self.on_open = lambda uris, token: None
        self.on_show_folders = lambda uris, token: None
        self.on_show_items = lambda uris, token: None
        self.on_show_properties = lambda uris, token: None
        # Asked to end, by `filehouse --quit`: takes nothing
        self.on_quit = lambda: None

    def claim(self):
        """Try to be the one process. True when this is it, False when another is and
        can be handed work with `send`, None when there is no bus to tell."""
        if self._connection is None:
            try:
                self._connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            except GLib.Error as error:
                log.debug("no session bus, so a process of its own: %s", error.message)
                return None
        # Registered first: a call may come the moment the name is ours
        if not self._register():
            return None
        answer = self._request_name(BUS_NAME)
        if answer is None:
            self._unregister()
            return None
        self.primary = answer in (_PRIMARY_OWNER, _ALREADY_OWNER)
        if not self.primary:
            self._unregister()
            return False
        # Somebody else may be the file manager already (Dolphin, when it runs): then
        # it stays that, and this is only the one process of this app
        self.file_manager = self._request_name(FILE_MANAGER_NAME) in (
            _PRIMARY_OWNER,
            _ALREADY_OWNER,
        )
        return True

    def send(self, uris, token=""):
        """Hand URIs to the process that runs. Whether it took them."""
        try:
            self._connection.call_sync(
                BUS_NAME,
                OBJECT_PATH,
                INTERFACE,
                "Open",
                GLib.Variant("(ass)", (list(uris), token)),
                None,
                Gio.DBusCallFlags.NO_AUTO_START,
                _SEND_TIMEOUT_MS,
                None,
            )
        except GLib.Error as error:
            log.warning("the running filehouse did not answer: %s", error.message)
            return False
        return True

    def ask_to_quit(self):
        """Tell the process that runs to end: at once when it has nothing open, and
        otherwise when its windows are closed and its jobs have ended. Whether it heard."""
        try:
            self._connection.call_sync(
                BUS_NAME,
                OBJECT_PATH,
                INTERFACE,
                "Quit",
                None,
                None,
                Gio.DBusCallFlags.NO_AUTO_START,
                _SEND_TIMEOUT_MS,
                None,
            )
        except GLib.Error as error:
            log.warning("the running filehouse did not answer: %s", error.message)
            return False
        return True

    def close(self):
        """Give the names back. Safe to call more than once."""
        if self._connection is None or not self.primary:
            return
        self._unregister()
        for name in (FILE_MANAGER_NAME, BUS_NAME):
            try:
                self._bus_call("ReleaseName", GLib.Variant("(s)", (name,)))
            except GLib.Error:
                pass
        self.primary = self.file_manager = False

    def _register(self):
        node = Gio.DBusNodeInfo.new_for_xml(_XML)
        register = getattr(
            self._connection, "register_object_with_closures2", self._connection.register_object
        )
        try:
            for path, interface in (
                (OBJECT_PATH, node.interfaces[0]),
                (FILE_MANAGER_PATH, node.interfaces[1]),
            ):
                self._registrations.append(register(path, interface, self._on_call, None, None))
        except GLib.Error as error:
            log.debug("could not be reached on the bus: %s", error.message)
            self._unregister()
            return False
        return True

    def _unregister(self):
        for registration in self._registrations:
            self._connection.unregister_object(registration)
        self._registrations = []

    def _request_name(self, name):
        try:
            reply = self._bus_call("RequestName", GLib.Variant("(su)", (name, _DO_NOT_QUEUE)))
        except GLib.Error as error:
            log.debug("could not ask for %s: %s", name, error.message)
            return None
        return reply.unpack()[0]

    def _bus_call(self, method, parameters):
        return self._connection.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            method,
            parameters,
            None,
            Gio.DBusCallFlags.NONE,
            _BUS_TIMEOUT_MS,
            None,
        )

    def _on_call(self, _connection, _sender, _path, interface, method, parameters, invocation):
        # Answered first: the caller waits for this, not for a window
        invocation.return_value(None)
        if (interface, method) == (INTERFACE, "Quit"):
            try:
                self.on_quit()
            except Exception:
                log.exception("%s failed", method)
            return
        uris, token = parameters.unpack()
        handler = {
            (INTERFACE, "Open"): self.on_open,
            (FILE_MANAGER_INTERFACE, "ShowFolders"): self.on_show_folders,
            (FILE_MANAGER_INTERFACE, "ShowItems"): self.on_show_items,
            (FILE_MANAGER_INTERFACE, "ShowItemProperties"): self.on_show_properties,
        }.get((interface, method))
        if handler is None:
            return
        try:
            handler(list(uris), token)
        except Exception:
            log.exception("%s failed", method)
