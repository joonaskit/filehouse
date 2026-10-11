"""Drives and other mounted volumes, for the sidebar: what is there, mounting and ejecting."""

from dataclasses import dataclass, field

from gi.repository import Gio, GLib
from PySide6.QtCore import QObject, Signal

_FALLBACK_ICON = "drive-removable-media"
_USAGE_ATTRIBUTES = "filesystem::size,filesystem::free"


@dataclass(frozen=True)
class Usage:
    """How full a drive is, in bytes."""

    used: int
    total: int

    @property
    def free(self):
        return self.total - self.used

    @property
    def fraction(self):
        return self.used / self.total if self.total else 0.0


@dataclass(frozen=True)
class Volume:
    name: str
    icon: str  # an icon theme name
    location: Gio.File | None  # where it is mounted, None when it isn't
    can_eject: bool  # ejected, or for something that can't be, unmounted
    eject_label: str  # "Eject" or "Unmount"
    _mount: object = field(default=None, repr=False, compare=False)
    _volume: object = field(default=None, repr=False, compare=False)


class Volumes(QObject):
    """Follows `Gio.VolumeMonitor`. Create it on the main thread, where its signals arrive."""

    changed = Signal()
    # A volume's folder is gone: it was ejected, unmounted or pulled out
    removed = Signal(object)
    # Something could not be mounted or ejected; the text is fit to show
    failed = Signal(str)
    # What `usage` answers has changed
    usage_changed = Signal()

    def __init__(self, parent=None, monitor=None, system=None):
        """`system` is the root of the drive the system is on, listed first. With the
        real monitor it is "/"; with a stand-in there is none unless one is given."""
        super().__init__(parent)
        if monitor is None and system is None:
            system = Gio.File.new_for_path("/")
        self._system = system
        self._usage = {}  # URI of a mounted volume's root -> Usage
        self._cancellable = Gio.Cancellable()
        # Through a plain function: Qt can't call a Gio method directly
        cancellable = self._cancellable
        self.destroyed.connect(lambda: cancellable.cancel())
        self._monitor = monitor or Gio.VolumeMonitor.get()
        self._handlers = [
            self._monitor.connect(signal, self._on_changed)
            for signal in (
                "mount-added",
                "mount-changed",
                "volume-added",
                "volume-removed",
                "volume-changed",
            )
        ]
        self._handlers.append(self._monitor.connect("mount-removed", self._on_mount_removed))
        self.destroyed.connect(self._disconnect)
        self.refresh_usage()

    def _disconnect(self):
        for handler in self._handlers:
            self._monitor.disconnect(handler)
        self._handlers = []

    def entries(self):
        """The system's drive, mounted volumes, then the ones that could be mounted."""
        result = []
        if self._system is not None:
            result.append(
                Volume(
                    name=GLib.get_os_info("NAME") or "Computer",
                    icon="drive-harddisk",
                    location=self._system,
                    can_eject=False,
                    eject_label="",
                )
            )
        for mount in self._monitor.get_mounts():
            if mount.is_shadowed():
                continue
            if self._system is not None and mount.get_root().equal(self._system):
                continue
            ejectable = mount.can_eject()
            result.append(
                Volume(
                    name=mount.get_name(),
                    icon=_icon_name(mount.get_icon()),
                    location=mount.get_root(),
                    can_eject=ejectable or mount.can_unmount(),
                    eject_label="Eject" if ejectable else "Unmount",
                    _mount=mount,
                )
            )
        for volume in self._monitor.get_volumes():
            if volume.get_mount() is None and volume.can_mount():
                result.append(
                    Volume(
                        name=volume.get_name(),
                        icon=_icon_name(volume.get_icon()),
                        location=None,
                        can_eject=False,
                        eject_label="",
                        _volume=volume,
                    )
                )
        return result

    def usage(self, volume):
        """How full a mounted volume is, or None when that isn't known (yet)."""
        if volume.location is None:
            return None
        return self._usage.get(volume.location.get_uri())

    def refresh_usage(self):
        """Ask every mounted volume how full it is. The answers come later, each with
        `usage_changed`."""
        mounted = {
            entry.location.get_uri(): entry.location
            for entry in self.entries()
            if entry.location is not None
        }
        gone = set(self._usage) - set(mounted)
        for uri in gone:
            del self._usage[uri]
        for uri, location in mounted.items():
            location.query_filesystem_info_async(
                _USAGE_ATTRIBUTES, GLib.PRIORITY_DEFAULT, self._cancellable, self._on_usage, uri
            )

    def _on_usage(self, location, result, uri):
        try:
            info = location.query_filesystem_info_finish(result)
        except GLib.Error:
            # Cancelled, gone, or a file system that doesn't say
            return
        total = info.get_attribute_uint64("filesystem::size")
        if not total:
            return
        usage = Usage(total - info.get_attribute_uint64("filesystem::free"), total)
        if self._usage.get(uri) != usage:
            self._usage[uri] = usage
            try:
                self.usage_changed.emit()
            except RuntimeError:
                # Answered after this was destroyed
                pass

    def mount(self, volume, on_mounted):
        """Mount a volume that isn't. `on_mounted(location)` is called when it is there."""

        def finished(source, result):
            try:
                source.mount_finish(result)
            except GLib.Error as error:
                self.failed.emit(f"Could not open {volume.name}: {error.message}")
                return
            mount = source.get_mount()
            if mount is not None:
                on_mounted(mount.get_root())

        volume._volume.mount(Gio.MountMountFlags.NONE, Gio.MountOperation(), None, finished)

    def eject(self, volume):
        """Eject a mounted volume, or unmount it when it can't be ejected."""
        mount = volume._mount
        if mount is None:
            return

        def finished(source, result, finish):
            try:
                finish(source, result)
            except GLib.Error as error:
                self.failed.emit(
                    f"Could not {volume.eject_label.lower()} {volume.name}: {error.message}"
                )

        operation, flags = Gio.MountOperation(), Gio.MountUnmountFlags.NONE
        if mount.can_eject():
            mount.eject_with_operation(
                flags, operation, None, finished, type(mount).eject_with_operation_finish
            )
        else:
            mount.unmount_with_operation(
                flags, operation, None, finished, type(mount).unmount_with_operation_finish
            )

    def _on_changed(self, *_args):
        self.changed.emit()
        self.refresh_usage()

    def _on_mount_removed(self, _monitor, mount):
        self.removed.emit(mount.get_root())
        self.changed.emit()
        self.refresh_usage()


def _icon_name(icon):
    if isinstance(icon, Gio.ThemedIcon):
        names = icon.get_names()
        if names:
            return names[0]
    return _FALLBACK_ICON
