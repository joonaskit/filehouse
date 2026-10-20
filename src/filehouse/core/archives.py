"""Reading and writing archives with libarchive (the `libarchive-c` binding).

libarchive is optional: without it `available()` is False and the app offers nothing
for archives. This module knows the library and what a path inside an archive may be;
the jobs that extract and compress are in `operations/archive.py`.

Nothing here trusts an archive. `safe_parts` is the one place that decides whether a
path from an archive may be used at all.
"""

import ctypes
import stat
from contextlib import contextmanager
from dataclasses import dataclass

from filehouse.core.operations.conflicts import split_extension

try:
    import libarchive
    from libarchive import ffi
    from libarchive.entry import ArchiveEntry
    from libarchive.exception import ArchiveError

    _lib = ffi.libarchive
    for _name in ("archive_entry_is_data_encrypted", "archive_entry_is_metadata_encrypted"):
        getattr(_lib, _name).argtypes = [ctypes.c_void_p]
        getattr(_lib, _name).restype = ctypes.c_int
    _lib.archive_entry_set_symlink.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    _lib.archive_entry_set_symlink.restype = None
except (ImportError, OSError, AttributeError):  # no binding, no library, or too old a one
    libarchive = None

    class ArchiveError(Exception):
        pass


FILE, FOLDER, SYMLINK, HARDLINK, OTHER = "file", "folder", "symlink", "hardlink", "other"

# What "Extract" is offered for: archives that hold files under names. A lone
# compressed file (.gz, .xz) is not one
_EXTRACTABLE = {
    "application/zip",
    "application/x-tar",
    "application/x-compressed-tar",
    "application/x-bzip2-compressed-tar",
    "application/x-xz-compressed-tar",
    "application/x-zstd-compressed-tar",
    "application/x-lzma-compressed-tar",
    "application/x-lz4-compressed-tar",
    "application/x-7z-compressed",
    "application/vnd.rar",
    "application/x-rar",
    "application/x-cpio",
    "application/java-archive",
}

_BLOCK = 64 * 1024


@dataclass(frozen=True)
class Format:
    key: str
    label: str
    extension: str
    format_name: str
    filter_name: str | None


# What "Compress" offers, the first being the default
FORMATS = (
    Format("zip", "Zip", ".zip", "zip", None),
    Format("tar.gz", "Tar, gzip", ".tar.gz", "pax_restricted", "gzip"),
)
_FORMATS = {format.key: format for format in FORMATS}


class ArchiveProblem(Exception):
    """The archive cannot be read or written: damaged, or of a kind libarchive lacks."""


class NeedsPassword(Exception):
    """The archive is protected, and no password or the wrong one was given."""


@dataclass(frozen=True)
class Entry:
    name: str  # as the archive has it, for messages
    parts: tuple | None  # the path's components, or None when it must not be used
    kind: str
    size: int = 0
    mode: int = 0
    mtime: int | None = None
    link: str = ""  # where a link points
    encrypted: bool = False


def available():
    return libarchive is not None


def can_extract(content_type):
    return available() and content_type in _EXTRACTABLE


def format_for(key):
    return _FORMATS[key]


def folder_name(archive_name):
    """The folder an archive's contents go in: its name without the extension."""
    stem, extension = split_extension(archive_name)
    return stem if extension and stem else f"{archive_name} (extracted)"


def archive_name(source_names, folder_name, format):
    """What to call an archive of these items, before the user changes it."""
    if len(source_names) == 1:
        stem = split_extension(source_names[0])[0] or source_names[0]
    else:
        stem = folder_name or "Archive"
    return stem + format.extension


def safe_parts(pathname):
    """The components of a path from an archive, or None when it is not to be trusted:
    an absolute path, or one that climbs out with "..". "./a//b/" is ("a", "b")."""
    if not pathname or pathname.startswith("/") or "\0" in pathname:
        return None
    parts = tuple(part for part in pathname.split("/") if part not in ("", "."))
    if not parts or ".." in parts:
        return None
    return parts


@contextmanager
def reading(path, passphrase=None):
    """Open an archive and give its entries, in order: `(Entry, blocks)`, where
    `blocks()` yields the content of a file. An entry's content can only be read
    before the next entry is asked for."""
    _need()
    try:
        with libarchive.file_reader(path, passphrase=passphrase or None) as archive:
            yield _entries(archive)
    except ArchiveError as error:
        raise _problem(error) from error


def _entries(archive):
    try:
        for raw in archive:
            yield _entry(raw), lambda raw=raw: _blocks(raw)
    except ArchiveError as error:
        raise _problem(error) from error


def _blocks(raw):
    try:
        yield from raw.get_blocks(_BLOCK)
    except ArchiveError as error:
        raise _problem(error) from error


def _entry(raw):
    name = _text(raw.pathname)
    pointer = raw._entry_p
    if raw.issym:
        kind = SYMLINK
    elif raw.islnk:
        kind = HARDLINK
    elif raw.isdir:
        kind = FOLDER
    elif raw.isreg:
        kind = FILE
    else:
        kind = OTHER
    encrypted = (
        _lib.archive_entry_is_data_encrypted(pointer) > 0
        or _lib.archive_entry_is_metadata_encrypted(pointer) > 0
    )
    return Entry(
        name=name,
        parts=safe_parts(name),
        kind=kind,
        size=max(0, raw.size or 0) if kind == FILE else 0,
        mode=stat.S_IMODE(raw.mode or 0),
        mtime=raw.mtime,
        link=_text(raw.linkpath) if kind in (SYMLINK, HARDLINK) else "",
        encrypted=encrypted,
    )


def _text(value):
    # A name that is not UTF-8 comes as bytes: keep them, as the file system would
    if isinstance(value, bytes):
        return value.decode("utf-8", "surrogateescape")
    return value or ""


def password_works(path, passphrase):
    """Whether the protected content of an archive can be read with this password."""
    try:
        with reading(path, passphrase) as entries:
            for entry, blocks in entries:
                if entry.encrypted and entry.kind == FILE and entry.size:
                    next(blocks(), None)
                    return True
    except NeedsPassword:
        return False
    return True


class Writer:
    """An archive being written to the open file `descriptor`, which the caller closes."""

    def __init__(self, descriptor, format):
        _need()
        self._descriptor = descriptor
        self._format = format
        self._context = None
        self._archive = None

    def __enter__(self):
        try:
            self._context = libarchive.fd_writer(
                self._descriptor, self._format.format_name, self._format.filter_name
            )
            self._archive = self._context.__enter__()
        except ArchiveError as error:
            raise _problem(error) from error
        return self

    def __exit__(self, *exception):
        try:
            return self._context.__exit__(*exception)
        except ArchiveError as error:
            if exception[0] is None:
                raise _problem(error) from error
            return False

    def add(self, name, status, link=None, blocks=()):
        """Add one item under `name`: a folder, a link to `link`, or a file whose
        content `blocks` yields, exactly `status.st_size` bytes of it."""
        entry = ArchiveEntry(header_codec="utf-8")
        entry.pathname = name
        entry.filetype = stat.S_IFMT(status.st_mode)
        entry.perm = stat.S_IMODE(status.st_mode)
        entry.mtime = int(status.st_mtime)
        entry.uid, entry.gid = status.st_uid, status.st_gid
        entry.size = status.st_size if stat.S_ISREG(status.st_mode) else 0
        if link is not None:
            _lib.archive_entry_set_symlink(entry._entry_p, link.encode("utf-8", "surrogateescape"))
        pointer = self._archive._pointer
        try:
            ffi.write_header(pointer, entry._entry_p)
            for block in blocks:
                ffi.write_data(pointer, block, len(block))
            ffi.write_finish_entry(pointer)
        except ArchiveError as error:
            raise _problem(error) from error


def _need():
    if libarchive is None:
        raise ArchiveProblem("archives need libarchive, which is not installed")


def _problem(error):
    message = (getattr(error, "msg", None) or str(error)).strip()
    lowered = message.lower()
    if "passphrase" in lowered or "password" in lowered:
        return NeedsPassword(message)
    if "encrypt" in lowered:
        return ArchiveProblem("its kind of password protection is not supported")
    return ArchiveProblem(message[:1].lower() + message[1:] if message else "cannot be read")
