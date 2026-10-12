"""Volumes and the sidebar, with stand-ins for Gio's volume monitor: no real drive is touched."""

import os

import pytest
from gi.repository import Gio, GLib
from PySide6.QtCore import Qt
from ui_helpers import gfile

from filehouse.core.locations import Place
from filehouse.qt_models.volumes import Volumes
from filehouse.ui.sidebar import Sidebar


class FakeMount:
    def __init__(self, name, root, ejectable=True, shadowed=False, error=None):
        self.name, self.root = name, gfile(root)
        self.ejectable, self.shadowed, self.error = ejectable, shadowed, error
        self.calls = []

    def get_name(self):
        return self.name

    def get_root(self):
        return self.root

    def get_icon(self):
        return Gio.ThemedIcon.new_with_default_fallbacks("drive-removable-media-usb")

    def is_shadowed(self):
        return self.shadowed

    def can_eject(self):
        return self.ejectable

    def can_unmount(self):
        return True

    def _finish(self, result):
        if self.error:
            raise GLib.Error.new_literal(Gio.io_error_quark(), self.error, Gio.IOErrorEnum.BUSY)

    eject_with_operation_finish = _finish
    unmount_with_operation_finish = _finish

    def eject_with_operation(self, _flags, _operation, _cancellable, callback, finish):
        self.calls.append("eject")
        callback(self, None, finish)

    def unmount_with_operation(self, _flags, _operation, _cancellable, callback, finish):
        self.calls.append("unmount")
        callback(self, None, finish)


class FakeVolume:
    def __init__(self, name, mounts_at=None, error=None):
        self.name, self.mounts_at, self.error = name, mounts_at, error
        self.mounted = None

    def get_name(self):
        return self.name

    def get_icon(self):
        return Gio.FileIcon.new(gfile("/tmp/icon.png"))

    def get_mount(self):
        return self.mounted

    def can_mount(self):
        return True

    def mount(self, _flags, _operation, _cancellable, callback):
        callback(self, None)

    def mount_finish(self, _result):
        if self.error:
            raise GLib.Error.new_literal(Gio.io_error_quark(), self.error, Gio.IOErrorEnum.FAILED)
        self.mounted = FakeMount(self.name, self.mounts_at)


class FakeMonitor:
    def __init__(self, mounts=(), volumes=()):
        self.mounts, self.volumes = list(mounts), list(volumes)
        self.handlers = {}

    def get_mounts(self):
        return self.mounts

    def get_volumes(self):
        return self.volumes

    def connect(self, signal, handler):
        self.handlers[signal] = handler
        return len(self.handlers)

    def disconnect(self, _handler):
        pass

    def emit(self, signal, *arguments):
        self.handlers[signal](self, *arguments)


def test_lists_mounts_and_what_could_be_mounted(qtbot, tmp_path):
    stick = FakeMount("USB stick", tmp_path / "stick")
    share = FakeMount("Share", tmp_path / "share", ejectable=False)
    hidden = FakeMount("Shadow", tmp_path / "shadow", shadowed=True)
    monitor = FakeMonitor([stick, share, hidden], [FakeVolume("Backup disk")])

    entries = Volumes(monitor=monitor).entries()

    assert [(e.name, e.eject_label, e.can_eject) for e in entries] == [
        ("USB stick", "Eject", True),
        ("Share", "Unmount", True),
        ("Backup disk", "", False),
    ]
    assert entries[0].location.get_path() == str(tmp_path / "stick")
    assert entries[0].icon == "drive-removable-media-usb"
    assert entries[2].location is None
    # An icon that is not a theme name falls back to a plain drive
    assert entries[2].icon == "drive-removable-media"


def test_follows_the_monitor(qtbot, tmp_path):
    monitor = FakeMonitor()
    volumes = Volumes(monitor=monitor)
    changes, removed = [], []
    volumes.changed.connect(lambda: changes.append(len(volumes.entries())))
    volumes.removed.connect(lambda root: removed.append(root.get_path()))

    stick = FakeMount("USB stick", tmp_path / "stick")
    monitor.mounts.append(stick)
    monitor.emit("mount-added", stick)
    monitor.mounts.remove(stick)
    monitor.emit("mount-removed", stick)

    assert changes == [1, 0]
    assert removed == [str(tmp_path / "stick")]


def test_eject_and_unmount(qtbot, tmp_path):
    stick = FakeMount("USB stick", tmp_path / "stick")
    share = FakeMount("Share", tmp_path / "share", ejectable=False)
    busy = FakeMount("Camera", tmp_path / "camera", error="Target is busy")
    volumes = Volumes(monitor=FakeMonitor([stick, share, busy]))
    failures = []
    volumes.failed.connect(failures.append)

    for entry in volumes.entries():
        volumes.eject(entry)

    assert stick.calls == ["eject"]
    assert share.calls == ["unmount"]
    assert failures == ["Could not eject Camera: Target is busy"]


def test_mounting_reports_where_it_is_or_why_not(qtbot, tmp_path):
    good = FakeVolume("Backup disk", mounts_at=tmp_path / "backup")
    locked = FakeVolume("Vault", error="A passphrase is needed")
    volumes = Volumes(monitor=FakeMonitor(volumes=[good, locked]))
    arrived, failures = [], []
    volumes.failed.connect(failures.append)

    for entry in volumes.entries():
        volumes.mount(entry, lambda location: arrived.append(location.get_path()))

    assert arrived == [str(tmp_path / "backup")]
    assert failures == ["Could not open Vault: A passphrase is needed"]


@pytest.fixture
def sidebar(qtbot, tmp_path):
    places = [Place("Home", "user-home", gfile(tmp_path)), Place("Docs", "folder", gfile("/docs"))]
    stick = FakeMount("USB stick", tmp_path / "stick")
    disk = FakeVolume("Backup disk", mounts_at=tmp_path / "backup")
    monitor = FakeMonitor([stick], [disk])
    sidebar = Sidebar(places, Volumes(monitor=monitor))
    qtbot.addWidget(sidebar)
    sidebar.monitor, sidebar.stick = monitor, stick
    return sidebar


def names(sidebar):
    return [sidebar.item(row).text() for row in range(sidebar.count())]


def test_sidebar_shows_places_then_devices_under_headings(sidebar):
    assert names(sidebar) == ["Places", "Home", "Docs", "Devices", "USB stick", "Backup disk"]
    assert [entry.name for entry in sidebar.entries()] == [
        "Home",
        "Docs",
        "USB stick",
        "Backup disk",
    ]
    assert sidebar.item(sidebar.row_of("Backup disk")).toolTip()
    assert not sidebar.item(sidebar.row_of("USB stick")).toolTip()
    # A heading is not a place: it can't be selected or found by name
    assert not sidebar.item(0).flags() & Qt.ItemFlag.ItemIsSelectable
    assert sidebar.row_of("Places") == -1


def test_no_devices_heading_without_devices(qtbot, tmp_path):
    sidebar = Sidebar([Place("Home", "user-home", gfile(tmp_path))], Volumes(monitor=FakeMonitor()))
    qtbot.addWidget(sidebar)

    assert names(sidebar) == ["Places", "Home"]


def test_sidebar_follows_drives_coming_and_going_and_keeps_its_highlight(sidebar, tmp_path):
    sidebar.select(gfile("/docs"))
    card = FakeMount("SD card", tmp_path / "card")

    sidebar.monitor.mounts.append(card)
    sidebar.monitor.emit("mount-added", card)

    assert [entry.name for entry in sidebar.entries()] == [
        "Home",
        "Docs",
        "USB stick",
        "SD card",
        "Backup disk",
    ]
    assert sidebar.currentRow() == sidebar.row_of("Docs")

    sidebar.select(gfile(tmp_path / "stick"))
    assert sidebar.currentRow() == sidebar.row_of("USB stick")
    sidebar.select(gfile("/elsewhere"))
    assert sidebar.currentRow() == -1


def test_clicking_goes_there_and_an_unmounted_drive_is_mounted_first(qtbot, sidebar, tmp_path):
    went = []
    sidebar.place_activated.connect(lambda location: went.append(location.get_path()))

    for name in ("Places", "Docs", "USB stick", "Backup disk"):
        row = 0 if name == "Places" else sidebar.row_of(name)
        rect = sidebar.visualItemRect(sidebar.item(row))
        qtbot.mouseClick(sidebar.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())

    assert went == ["/docs", str(tmp_path / "stick"), str(tmp_path / "backup")]


def test_the_system_drive_is_listed_first_and_not_twice(qtbot, tmp_path):
    system = gfile(tmp_path)
    monitor = FakeMonitor([FakeMount("Same place", tmp_path), FakeMount("USB stick", "/stick")])
    volumes = Volumes(monitor=monitor, system=system)

    entries = volumes.entries()

    assert [entry.name for entry in entries] == [GLib.get_os_info("NAME"), "USB stick"]
    assert entries[0].location.equal(system)
    # Not something to eject
    assert not entries[0].can_eject


def test_a_stand_in_monitor_has_no_system_drive_unless_given(qtbot):
    assert Volumes(monitor=FakeMonitor()).entries() == []


def test_usage_of_mounted_volumes_arrives_later(qtbot, tmp_path):
    from filehouse.qt_models.volumes import Usage

    stick = FakeMount("USB stick", tmp_path)
    disk = FakeVolume("Backup disk", mounts_at=tmp_path / "backup")
    volumes = Volumes(monitor=FakeMonitor([stick], [disk]))
    mounted, unmounted = volumes.entries()
    assert volumes.usage(mounted) is None

    qtbot.waitUntil(lambda: volumes.usage(mounted) is not None, timeout=5000)

    usage = volumes.usage(mounted)
    disk_stats = os.statvfs(tmp_path)
    assert usage.total == disk_stats.f_blocks * disk_stats.f_frsize
    assert 0 < usage.used <= usage.total and 0.0 < usage.fraction <= 1.0
    assert usage.free == usage.total - usage.used
    # Nothing is known about a drive that isn't mounted
    assert volumes.usage(unmounted) is None
    assert Usage(used=1, total=4).fraction == 0.25 and Usage(0, 0).fraction == 0.0


def test_usage_of_a_drive_that_is_gone_is_forgotten(qtbot, tmp_path):
    stick = FakeMount("USB stick", tmp_path)
    monitor = FakeMonitor([stick])
    volumes = Volumes(monitor=monitor)
    entry = volumes.entries()[0]
    qtbot.waitUntil(lambda: volumes.usage(entry) is not None, timeout=5000)

    monitor.mounts.remove(stick)
    monitor.emit("mount-removed", stick)

    assert volumes.usage(entry) is None


def test_sidebar_shows_how_full_a_drive_is(qtbot, tmp_path):
    monitor = FakeMonitor([FakeMount("USB stick", tmp_path)])
    volumes = Volumes(monitor=monitor)
    sidebar = Sidebar([Place("Home", "user-home", gfile(tmp_path))], volumes)
    qtbot.addWidget(sidebar)
    sidebar.resize(220, 300)
    sidebar.show()

    qtbot.waitUntil(lambda: sidebar.usage_shown("USB stick") is not None, timeout=5000)

    assert sidebar.usage_shown("USB stick") == volumes.usage(volumes.entries()[0])
    assert " free of " in sidebar.item(sidebar.row_of("USB stick")).toolTip()
    # A place is not a drive
    assert sidebar.usage_shown("Home") is None
    # Drawn without trouble, as a list and as a rail
    assert not sidebar.grab().isNull()
    sidebar.set_rail(True)
    assert sidebar.usage_shown("USB stick") is not None
    assert sidebar.item(sidebar.row_of("USB stick")).toolTip().startswith("USB stick: ")
    assert not sidebar.grab().isNull()
