"""Drives and other mounted volumes, for the sidebar: what is there, mounting and ejecting."""

from dataclasses import dataclass, field

from gi.repository import Gio, GLib
from PySide6.QtCore import QObject, Signal

_FALLBACK_ICON = "drive-removable-media"


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

    def __init__(self, parent=None, monitor=None):
        super().__init__(parent)
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

    def _disconnect(self):
        for handler in self._handlers:
            self._monitor.disconnect(handler)
        self._handlers = []

    def entries(self):
        """Mounted volumes first, then the ones that could be mounted."""
        result = []
        for mount in self._monitor.get_mounts():
            if mount.is_shadowed():
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

    def _on_mount_removed(self, _monitor, mount):
        self.removed.emit(mount.get_root())
        self.changed.emit()


def _icon_name(icon):
    if isinstance(icon, Gio.ThemedIcon):
        names = icon.get_names()
        if names:
            return names[0]
    return _FALLBACK_ICON
