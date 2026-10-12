"""Recent files: the freedesktop list, Plasma's lists over D-Bus, and when neither is touched."""

import shutil
import subprocess

import pytest
from gi.repository import Gio, GLib

from filehouse.integration import recent
from filehouse.integration.recent import BUS_NAME, INTERFACE, OBJECT_PATH, RecentFiles

TIMEOUT = 5000
APP = "filehouse"


def location(path):
    return Gio.File.new_for_path(str(path))


@pytest.fixture
def document(tmp_path):
    path = tmp_path / "report.pdf"
    path.write_text("x")
    return path


@pytest.fixture
def list_path(tmp_path):
    return str(tmp_path / "data" / "recently-used.xbel")


def make(list_path, **kwargs):
    kwargs.setdefault("plasma", lambda _app: True)
    return RecentFiles(app_id=APP, path=list_path, **kwargs)


def settle(qtbot, files):
    qtbot.waitUntil(lambda: not files.busy, timeout=TIMEOUT)


def load(list_path):
    bookmarks = GLib.BookmarkFile()
    bookmarks.load_from_file(list_path)
    return bookmarks


# Plasma's own setting


def write_plasma(config_dir, **keys):
    lines = "\n".join(f"{name}={value}" for name, value in keys.items())
    (config_dir / "kactivitymanagerd-pluginsrc").write_text(
        f"[Plugin-org.kde.ActivityManager.Resources.Scoring]\n{lines}\n"
    )


@pytest.fixture
def config_dir(tmp_path):
    folder = tmp_path / "config"
    folder.mkdir()
    return folder


def test_without_a_setting_plasma_lets_it_be(config_dir):
    assert recent.plasma_allows(APP, str(config_dir))


@pytest.mark.parametrize(
    "keys, allowed",
    [
        ({"what-to-remember": 0}, True),
        ({"what-to-remember": 2}, False),
        ({"what-to-remember": 0, "blocked-applications": f"org.kde.dolphin,{APP}"}, False),
        ({"what-to-remember": 0, "blocked-applications": "org.kde.dolphin"}, True),
        ({"what-to-remember": 1}, False),
        ({"what-to-remember": 1, "allowed-applications": f"{APP},org.kde.kate"}, True),
        ({"what-to-remember": 1, "allowed-applications": "org.kde.kate"}, False),
        ({"blocked-by-default": "true"}, False),
        ({"blocked-by-default": "true", "allowed-applications": APP}, True),
        ({"what-to-remember": "not a number"}, True),
        ({"keep-history-for": 3}, True),
    ],
)
def test_plasmas_setting(config_dir, keys, allowed):
    write_plasma(config_dir, **keys)

    assert recent.plasma_allows(APP, str(config_dir)) is allowed


# The freedesktop list


def test_an_opened_file_is_added_to_the_list(qtbot, document, list_path):
    files = make(list_path)

    assert files.add(location(document), "application/pdf")
    settle(qtbot, files)

    bookmarks = load(list_path)
    uri = location(document).get_uri()
    assert bookmarks.get_uris() == [uri]
    assert bookmarks.get_mime_type(uri) == "application/pdf"
    assert bookmarks.has_application(uri, APP)
    assert not bookmarks.get_is_private(uri)
    assert bookmarks.get_application_info(uri, APP)[2] == 1


def test_opening_it_again_counts_and_keeps_the_others(qtbot, tmp_path, document, list_path):
    other = tmp_path / "notes.txt"
    other.write_text("x")
    files = make(list_path)
    files.add(location(document), "application/pdf")
    files.add(location(other), "text/plain")
    files.add(location(document), "application/pdf")
    settle(qtbot, files)

    bookmarks = load(list_path)
    assert sorted(bookmarks.get_uris()) == sorted(
        [location(document).get_uri(), location(other).get_uri()]
    )
    assert bookmarks.get_application_info(location(document).get_uri(), APP)[2] == 2


def test_what_other_apps_wrote_is_kept(qtbot, document, list_path):
    other = GLib.BookmarkFile()
    other.set_mime_type("file:///home/me/old.odt", "application/vnd.oasis.opendocument.text")
    other.add_application("file:///home/me/old.odt", "libreoffice", "soffice %u")
    shutil.os.makedirs(shutil.os.path.dirname(list_path))
    other.to_file(list_path)
    files = make(list_path)

    files.add(location(document), "application/pdf")
    settle(qtbot, files)

    bookmarks = load(list_path)
    assert "file:///home/me/old.odt" in bookmarks.get_uris()
    assert bookmarks.has_application("file:///home/me/old.odt", "libreoffice")
    assert len(bookmarks.get_uris()) == 2


def test_a_list_that_cannot_be_read_is_left_alone(qtbot, document, list_path):
    shutil.os.makedirs(shutil.os.path.dirname(list_path))
    with open(list_path, "w") as f:
        f.write("this is not xml")
    files = make(list_path)

    files.add(location(document), "application/pdf")
    settle(qtbot, files)

    with open(list_path) as f:
        assert f.read() == "this is not xml"


def test_the_list_is_kept_to_the_newest(qtbot, tmp_path, list_path, monkeypatch):
    monkeypatch.setattr(recent, "_MAX_ENTRIES", 3)
    files = make(list_path)
    paths = []
    for index in range(5):
        path = tmp_path / f"file{index}.txt"
        path.write_text("x")
        paths.append(path)
        files.add(location(path), "text/plain")
        settle(qtbot, files)
        # Each one a second after the one before: the list is told in seconds
        bookmarks = load(list_path)
        bookmarks.set_modified_date_time(
            location(path).get_uri(), GLib.DateTime.new_from_unix_utc(1_700_000_000 + index)
        )
        bookmarks.to_file(list_path)

    assert len(load(list_path).get_uris()) == 3
    assert location(paths[-1]).get_uri() in load(list_path).get_uris()
    assert location(paths[0]).get_uri() not in load(list_path).get_uris()


# What is not remembered


def test_nothing_is_written_when_the_user_would_rather_not(qtbot, document, list_path):
    files = make(list_path, remember=lambda: False)

    assert not files.add(location(document), "application/pdf")
    settle(qtbot, files)

    assert not shutil.os.path.exists(list_path)


def test_nothing_is_written_when_plasma_says_no(qtbot, document, list_path):
    files = make(list_path, plasma=lambda _app: False)

    assert not files.add(location(document), "application/pdf")
    settle(qtbot, files)

    assert not shutil.os.path.exists(list_path)


def test_the_switch_is_asked_each_time(qtbot, document, list_path):
    private = [True]
    files = make(list_path, remember=lambda: not private[0])

    assert not files.add(location(document), "application/pdf")
    private[0] = False
    assert files.add(location(document), "application/pdf")
    settle(qtbot, files)


def test_folders_missing_files_and_places_off_this_computer_are_not_remembered(
    qtbot, tmp_path, list_path
):
    files = make(list_path)

    assert not files.wanted(location(tmp_path))
    assert not files.wanted(location(tmp_path / "missing.txt"))
    assert not files.wanted(Gio.File.new_for_uri("sftp://host/home/me/file.txt"))
    assert not files.wanted(Gio.File.new_for_uri("trash:///file.txt"))
    assert not shutil.os.path.exists(list_path)


# Plasma's lists, over D-Bus

_XML = f"""
<node>
  <interface name="{INTERFACE}">
    <method name="RegisterResourceEvent">
      <arg type="s" direction="in"/><arg type="u" direction="in"/>
      <arg type="s" direction="in"/><arg type="u" direction="in"/>
    </method>
    <method name="RegisterResourceMimetype">
      <arg type="s" direction="in"/><arg type="s" direction="in"/>
    </method>
  </interface>
</node>
"""


@pytest.fixture
def bus_address():
    """A session bus of our own: the real Plasma is never told."""
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


class FakeActivities:
    def __init__(self, address):
        self.connection = connect(address)
        self.calls = []
        node = Gio.DBusNodeInfo.new_for_xml(_XML)
        self.connection.register_object(OBJECT_PATH, node.interfaces[0], self._on_call, None, None)
        Gio.bus_own_name_on_connection(self.connection, BUS_NAME, Gio.BusNameOwnerFlags.NONE)

    def _on_call(self, _connection, _sender, _path, _interface, method, parameters, invocation):
        self.calls.append((method, parameters.unpack()))
        invocation.return_value(None)


def test_plasma_is_told_the_file_and_its_type(qtbot, document, list_path, bus_address):
    service = FakeActivities(bus_address)
    files = make(list_path, connection=connect(bus_address))

    files.add(location(document), "application/pdf")

    qtbot.waitUntil(lambda: len(service.calls) == 2, timeout=TIMEOUT)
    uri = location(document).get_uri()
    assert ("RegisterResourceEvent", (APP, 0, uri, 0)) in service.calls
    assert ("RegisterResourceMimetype", (uri, "application/pdf")) in service.calls
    settle(qtbot, files)


def test_plasma_is_not_told_what_is_not_remembered(qtbot, document, list_path, bus_address):
    service = FakeActivities(bus_address)
    files = make(list_path, connection=connect(bus_address), remember=lambda: False)

    files.add(location(document), "application/pdf")
    qtbot.wait(300)

    assert service.calls == []


def test_without_plasma_the_list_is_still_written(qtbot, document, list_path, bus_address):
    # A bus with nobody on it
    files = make(list_path, connection=connect(bus_address))

    files.add(location(document), "application/pdf")
    settle(qtbot, files)
    qtbot.wait(200)

    assert load(list_path).get_uris() == [location(document).get_uri()]


def test_without_a_bus_nothing_breaks(qtbot, document, list_path):
    files = make(list_path)

    assert files.add(location(document), "application/pdf")
    settle(qtbot, files)


# Taking files off the list


def test_files_are_taken_off_the_list_and_stay_on_disk(qtbot, tmp_path, document, list_path):
    other = tmp_path / "notes.txt"
    other.write_text("x")
    files = make(list_path)
    files.add(location(document), "application/pdf")
    files.add(location(other), "text/plain")
    settle(qtbot, files)
    done = []

    files.forget([location(document)], lambda: done.append(True))
    qtbot.waitUntil(lambda: bool(done), timeout=TIMEOUT)

    assert load(list_path).get_uris() == [location(other).get_uri()]
    assert document.exists()


def test_taking_off_what_is_not_there_is_fine(qtbot, tmp_path, document, list_path):
    files = make(list_path)
    files.add(location(document), "application/pdf")
    settle(qtbot, files)
    done = []

    files.forget([location(tmp_path / "never-there.txt")], lambda: done.append(True))
    qtbot.waitUntil(lambda: bool(done), timeout=TIMEOUT)

    assert load(list_path).get_uris() == [location(document).get_uri()]


def test_a_list_that_cannot_be_read_is_not_touched_by_taking_off(qtbot, document, list_path):
    shutil.os.makedirs(shutil.os.path.dirname(list_path))
    with open(list_path, "w") as f:
        f.write("this is not xml")
    files = make(list_path)
    done = []

    files.forget([location(document)], lambda: done.append(True))
    qtbot.waitUntil(lambda: bool(done), timeout=TIMEOUT)

    with open(list_path) as f:
        assert f.read() == "this is not xml"


def test_the_list_can_be_emptied_whoever_wrote_what_is_on_it(qtbot, tmp_path, document, list_path):
    other = GLib.BookmarkFile()
    other.set_mime_type("file:///home/me/old.odt", "application/vnd.oasis.opendocument.text")
    other.add_application("file:///home/me/old.odt", "libreoffice", "soffice %u")
    shutil.os.makedirs(shutil.os.path.dirname(list_path))
    other.to_file(list_path)
    files = make(list_path)
    files.add(location(document), "application/pdf")
    settle(qtbot, files)
    done = []

    files.clear(lambda: done.append(True))
    qtbot.waitUntil(lambda: bool(done), timeout=TIMEOUT)

    assert load(list_path).get_uris() == []
    assert document.exists()


def test_a_list_that_cannot_be_read_is_not_cleared(qtbot, list_path):
    shutil.os.makedirs(shutil.os.path.dirname(list_path))
    with open(list_path, "w") as f:
        f.write("this is not xml")
    files = make(list_path)
    done = []

    files.clear(lambda: done.append(True))
    qtbot.waitUntil(lambda: bool(done), timeout=TIMEOUT)

    with open(list_path) as f:
        assert f.read() == "this is not xml"
