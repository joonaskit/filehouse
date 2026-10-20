"""Extracting an archive and compressing into one, each as a job.

They are jobs like copy and move, with the same states, progress, questions and
errors, but an archive is read and written from start to end, so they do not plan
steps: there is nothing to retry in the middle of a stream.

What keeps them safe:

- Nothing that exists is ever replaced. An extraction makes one new item in the
  destination (the archive's only top-level item, or a folder named after the archive
  when it has several) and writes only inside it; an archive is written under a
  temporary name and put in place when it is complete. A name that is taken asks.
- A path from an archive is used only when `archives.safe_parts` accepts it, and every
  folder on the way to a file is opened without following links, so a link from the
  archive cannot be written through.
- Cancel removes the file being written, nothing else.
- Compressing reads its sources and never changes them.
"""

import errno
import os
import secrets
import stat
from dataclasses import replace

from gi.repository import Gio, GLib

from filehouse.core import archives, locations
from filehouse.core.errors import OperationError, has_code
from filehouse.core.operations import conflicts
from filehouse.core.operations.conflicts import Answer
from filehouse.core.operations.job import _VERBS, Job, JobKind, JobState

_BLOCK = 64 * 1024
_FOLDER_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_NEW_FILE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
_PLACE = Gio.FileCopyFlags.NOFOLLOW_SYMLINKS | Gio.FileCopyFlags.NO_FALLBACK_FOR_MOVE
_TEMP_PREFIX, _TEMP_SUFFIX = ".filehouse-", ".part"

_NOT_A_FOLDER = "its place is a link or a file, not a folder, so it was not extracted"
_REASONS = {
    errno.EACCES: "permission denied",
    errno.EPERM: "permission denied",
    errno.ENOSPC: "no space left on device",
    errno.EDQUOT: "no space left on device",
    errno.ENAMETOOLONG: "file name too long",
    errno.ENOENT: "no such file or folder",
    errno.EEXIST: "already exists",
    errno.EROFS: "read-only file system",
    # A folder on the way is a link, or a file: O_NOFOLLOW and O_DIRECTORY said no
    errno.ELOOP: _NOT_A_FOLDER,
    errno.ENOTDIR: _NOT_A_FOLDER,
}
# Nothing more can be written after these
_FATAL = (errno.ENOSPC, errno.EDQUOT, errno.EROFS)


def _reason(error):
    return _REASONS.get(error.errno) or (error.strerror or str(error)).lower()


def _umask():
    # Reading it by setting it would change it for the other threads for a moment
    try:
        with open("/proc/self/status") as status:
            for line in status:
                if line.startswith("Umask:"):
                    return int(line.split()[1], 8)
    except (OSError, ValueError, IndexError):
        pass
    return 0o022


def _open_folder(base, parts, create):
    """A descriptor for the folder `parts` below `base`, never through a link. With
    `create`, folders that are missing are made. The caller closes it."""
    folder = os.dup(base)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, 0o777, dir_fd=folder)
                except FileExistsError:
                    pass
            child = os.open(part, _FOLDER_FLAGS, dir_fd=folder)
            os.close(folder)
            folder = child
    except BaseException:
        os.close(folder)
        raise
    return folder


class _ArchiveJob(Job):
    def __init__(self, kind, sources, destination, prompt, on_change):
        super().__init__(kind, sources, destination, prompt=prompt, on_change=on_change)
        self.result = None  # what the job made, once it has
        self._complete = False
        self._files_done = 0
        self._files_total = 0
        self._bytes_done = 0

    @property
    def can_retry(self):
        return False

    def retry(self):
        return None

    def _path(self, location):
        path = location.get_path()
        if path is None:
            raise OperationError.at(
                location, self.kind.value, "archives work only with files on this computer"
            )
        return path

    def _free_name(self, name, is_dir, existing):
        """Something has `name` in the destination: the name to use instead, if the user
        wants one. Replacing is never offered."""
        taken = self.destination.get_child(name)
        reply = self._ask(conflicts.NameTaken(taken, existing == Gio.FileType.DIRECTORY))
        if reply is None:
            raise OperationError.at(taken, self.kind.value, "already exists")
        if reply.answer is not Answer.KEEP_BOTH:
            self._cancel_from_answer()
        return conflicts.free_name(self.destination, name, is_dir, self.cancellable)

    def _count(self, files=0, size=0):
        self._files_done += files
        self._bytes_done += size
        self._set_progress(files_done=self._files_done, bytes_done=self._bytes_done)

    def _finish(self, cancelled):
        planned = self.progress.planned
        if not cancelled and not self.errors and not self._complete:
            # Unreachable by design, but `done` must never be a guess
            self.errors.append(OperationError("", self.kind.value, "did not finish"))
        done, total = self._files_done, self._files_total
        of_total = f"{done} of {total} file{'' if total == 1 else 's'}"
        verb = _VERBS[self.kind]
        if cancelled:
            state = JobState.CANCELLED
            self.summary = f"Cancelled after {of_total}" if planned else "Cancelled"
        elif self.errors:
            state = JobState.FAILED
            if not planned:
                self.summary = str(self.errors[0])
            elif total > done:
                self.summary = f"{verb} {of_total}, {total - done} left out"
            else:
                self.summary = f"{verb} {of_total}, {len(self.errors)} failed"
        else:
            state = JobState.DONE
            self.summary = f"{verb} {done} file{'' if done == 1 else 's'}"
        self.progress = replace(
            self.progress, current="", files_done=done, bytes_done=self._bytes_done
        )
        self._set_state(state)


class ExtractJob(_ArchiveJob):
    """Extract the archive `archive` into the folder `destination`.

    `prompt` also answers `conflicts.Password` and `conflicts.NameTaken`. Without one,
    a protected archive and a taken name both fail the job before anything is written.
    """

    def __init__(self, archive, destination, *, prompt=None, on_change=None):
        super().__init__(JobKind.EXTRACT, [archive], destination, prompt, on_change)
        self._root = ""
        self._wrap = False
        self._root_is_dir = False
        self._folders = []  # (parts, mode, mtime) to set once everything is inside
        self._umask = 0o022

    def _work(self):
        archive = self.sources[0]
        path, folder = self._path(archive), self._path(self.destination)
        entries, passphrase = self._unlock(archive, path)

        used = [entry for entry in entries if entry.parts is not None]
        tops = {entry.parts[0] for entry in used}
        self._wrap = len(tops) != 1
        self._root = (
            archives.folder_name(archive.get_basename()) if self._wrap else next(iter(tops))
        )
        self._root_is_dir = self._wrap or any(
            len(entry.parts) > 1 or entry.kind == archives.FOLDER for entry in used
        )
        self._files_total = sum(entry.kind != archives.FOLDER for entry in entries)
        self._set_progress(
            files_total=self._files_total,
            bytes_total=sum(entry.size for entry in entries),
            planned=True,
        )
        self._set_state(JobState.RUNNING)
        self._umask = _umask()
        if not used:
            # Nothing that may be written: make nothing at all
            if not entries:
                raise OperationError.at(archive, "extract", "is empty")
            for entry in entries:
                self._refuse(entry)
            return

        # Advisory: the name is checked again when it is made
        existing = locations.file_type(self.destination.get_child(self._root), self.cancellable)
        if existing is not None:
            self._root = self._free_name(self._root, self._root_is_dir, existing)

        try:
            base = os.open(folder, _FOLDER_FLAGS & ~os.O_NOFOLLOW)
        except OSError as error:
            raise OperationError.at(self.destination, "extract", _reason(error)) from error
        try:
            if self._root_is_dir:
                root = self._make_root(base)
                os.close(base)
                base = root
            self.result = self.destination.get_child(self._root)
            self._extract_all(path, passphrase, base)
            self._finish_folders(base)
        finally:
            os.close(base)
        self._complete = True

    # Before anything is written

    def _unlock(self, archive, path):
        """List the archive, asking for its password as often as it takes."""
        passphrase, asked = None, False
        while True:
            try:
                entries = self._list(path, passphrase)
                protected = any(entry.encrypted for entry in entries)
                # Tried without one too: a kind of protection libarchive cannot open
                # (7z, rar) says so here, before a password is asked for in vain
                if not protected or archives.password_works(path, passphrase):
                    return entries, passphrase
            except archives.NeedsPassword:
                pass
            except archives.ArchiveProblem as problem:
                raise OperationError.at(archive, "extract", str(problem)) from problem
            reply = self._ask(conflicts.Password(archive, wrong=asked))
            if reply is None:
                raise OperationError.at(archive, "extract", "is protected with a password")
            if reply.answer is not Answer.PASSWORD:
                self._cancel_from_answer()
            passphrase, asked = reply.text, True

    def _list(self, path, passphrase):
        listed = []
        with archives.reading(path, passphrase) as entries:
            for entry, _blocks in entries:
                self._check_cancelled()
                listed.append(entry)
        return listed

    def _make_root(self, base):
        """Make the folder everything goes in. It must be new: what is in a folder
        that was there is not ours to write among."""
        while True:
            try:
                os.mkdir(self._root, 0o777, dir_fd=base)
            except FileExistsError:
                self._root = self._free_name(self._root, True, self._type_of_root())
                continue
            except OSError as error:
                raise OperationError(self._shown(()), "extract", _reason(error)) from error
            return os.open(self._root, _FOLDER_FLAGS, dir_fd=base)

    def _type_of_root(self):
        return locations.file_type(self.destination.get_child(self._root)) or Gio.FileType.REGULAR

    # Writing

    def _extract_all(self, path, passphrase, base):
        try:
            with archives.reading(path, passphrase) as entries:
                for entry, blocks in entries:
                    self._check_cancelled()
                    self._set_progress(current=entry.name.rstrip("/").rpartition("/")[2])
                    self._extract_one(entry, blocks, base)
        except archives.NeedsPassword as problem:
            raise OperationError.at(
                self.sources[0], "extract", "the password does not open all of it"
            ) from problem
        except archives.ArchiveProblem as problem:
            # The stream is broken: what comes after cannot be read either
            name = self.progress.current or self.sources[0].get_basename()
            raise OperationError(name, "extract", f"the archive is damaged ({problem})") from None

    def _refuse(self, entry):
        self.errors.append(
            OperationError(entry.name, "extract", "has an unsafe path, so it was not extracted")
        )

    def _extract_one(self, entry, blocks, base):
        if entry.parts is None:
            self._refuse(entry)
            return
        parts = self._below_root(entry.parts)
        while True:
            try:
                self._write(entry, blocks, base, parts)
            except FileExistsError:
                if self._root_is_dir:
                    self.errors.append(
                        OperationError(self._shown(parts), "extract", "is in the archive twice")
                    )
                    return
                # The one item of the archive, and its name was taken meanwhile
                self._root = self._free_name(self._root, False, self._type_of_root())
                parts = (self._root,)
                self.result = self.destination.get_child(self._root)
                continue
            except OperationError as error:
                self.errors.append(error)
            except OSError as error:
                failure = OperationError(self._shown(parts), "extract", _reason(error))
                if error.errno in _FATAL:
                    raise failure from error
                self.errors.append(failure)
            else:
                if entry.kind != archives.FOLDER:
                    self._count(files=1)
            return

    def _below_root(self, parts):
        """Where an entry goes, below the folder the descriptor is of: the root folder,
        or the destination when the archive holds a single file."""
        if not self._root_is_dir:
            return (self._root,)
        return parts if self._wrap else parts[1:]

    def _shown(self, parts):
        """The path of what is written, for messages."""
        folder = self.destination.get_parse_name().rstrip("/")
        if not self._root_is_dir:
            return f"{folder}/{self._root}"
        return "/".join((folder, self._root, *parts))

    def _write(self, entry, blocks, base, parts):
        if not parts:
            # The root folder itself, which is made already
            self._folders.append(((), entry.mode, entry.mtime))
            return
        name = parts[-1]
        parent = _open_folder(base, parts[:-1], create=True)
        try:
            if entry.kind == archives.FOLDER:
                try:
                    os.mkdir(name, 0o777, dir_fd=parent)
                except FileExistsError:
                    # Made for something inside it, or listed twice. A link is refused
                    os.close(os.open(name, _FOLDER_FLAGS, dir_fd=parent))
                self._folders.append((parts, entry.mode, entry.mtime))
            elif entry.kind == archives.FILE:
                self._write_file(entry, blocks, parent, name)
            elif entry.kind == archives.SYMLINK:
                # Made, never followed: what it points at is not written to
                os.symlink(entry.link, name, dir_fd=parent)
            elif entry.kind == archives.HARDLINK:
                self._hardlink(entry, base, parent, name, parts)
            else:
                raise OperationError(
                    self._shown(parts), "extract", "is a special file, so it was not extracted"
                )
        finally:
            os.close(parent)

    def _write_file(self, entry, blocks, parent, name):
        descriptor = os.open(name, _NEW_FILE_FLAGS, 0o600, dir_fd=parent)
        written = 0
        try:
            for block in blocks():
                self._check_cancelled()
                view = memoryview(block)
                while view:
                    view = view[os.write(descriptor, view) :]
                written += len(block)
                self._set_progress(bytes_done=self._bytes_done + written)
            os.fchmod(descriptor, (entry.mode or 0o644) & 0o777 & ~self._umask)
            if entry.mtime is not None:
                os.utime(descriptor, (entry.mtime, entry.mtime))
        except BaseException:
            # Ours, made just now: the only thing a cancel or a failure removes
            os.close(descriptor)
            try:
                os.unlink(name, dir_fd=parent)
            except OSError:
                pass
            self._set_progress(bytes_done=self._bytes_done)
            raise
        os.close(descriptor)
        self._count(size=written)

    def _hardlink(self, entry, base, parent, name, parts):
        target = archives.safe_parts(entry.link)
        if target is None:
            raise OperationError(
                self._shown(parts), "extract", "links to an unsafe path, so it was not extracted"
            )
        target = self._below_root(target)
        if not target:
            raise OperationError(self._shown(parts), "extract", "links to a folder")
        folder = _open_folder(base, target[:-1], create=False)
        try:
            os.link(target[-1], name, src_dir_fd=folder, dst_dir_fd=parent, follow_symlinks=False)
        finally:
            os.close(folder)

    def _finish_folders(self, base):
        """Folders get their permissions and times last, deepest first, so one that
        is read-only can still be filled. Best effort, as for a copied folder."""
        for parts, mode, mtime in sorted(self._folders, key=lambda item: -len(item[0])):
            try:
                folder = _open_folder(base, parts, create=False)
            except OSError:
                continue
            try:
                if mode:
                    # Always the owner's to open and to remove things from
                    os.fchmod(folder, (mode & 0o777 & ~self._umask) | 0o700)
                if mtime is not None:
                    os.utime(folder, (mtime, mtime))
            except OSError:
                pass
            finally:
                os.close(folder)


class CompressJob(_ArchiveJob):
    """Compress `sources` into the archive `name` in the folder `destination`, in the
    format `format` (a key of `archives.FORMATS`). The sources are only read."""

    def __init__(self, sources, destination, name, format, *, prompt=None, on_change=None):
        super().__init__(JobKind.COMPRESS, sources, destination, prompt, on_change)
        self._name = name
        self._format = archives.format_for(format)

    def _work(self):
        folder = self._path(self.destination)
        name = self._name.strip()
        if not name or "/" in name or name in (".", ".."):
            raise OperationError("", "compress", "the archive needs a name")
        items = self._plan()
        self._files_total = sum(not stat.S_ISDIR(status.st_mode) for _p, _n, status in items)
        self._files_total += len(self.errors)
        self._set_progress(
            files_total=self._files_total,
            bytes_total=sum(
                status.st_size for _p, _n, status in items if stat.S_ISREG(status.st_mode)
            ),
            planned=True,
        )
        self._set_state(JobState.RUNNING)

        # Advisory: checked again when the archive is put in place
        existing = locations.file_type(self.destination.get_child(name), self.cancellable)
        if existing is not None:
            name = self._free_name(name, False, existing)

        temp, descriptor = self._temp(folder)
        try:
            with archives.Writer(descriptor, self._format) as writer:
                for path, arcname, status in items:
                    self._check_cancelled()
                    self._set_progress(current=os.path.basename(path))
                    self._add(writer, path, arcname, status)
            if self.errors and not self._files_done:
                # Nothing could be read: an empty archive would only mislead
                raise self.errors.pop(0)
            if not _is_ours(temp, descriptor):
                raise OperationError(temp, "compress", "was replaced while it was being written")
            self.result = self._place(temp, name)
        except archives.ArchiveProblem as problem:
            _discard(temp, descriptor)
            raise OperationError(os.path.join(folder, name), "compress", str(problem)) from problem
        except BaseException:
            _discard(temp, descriptor)
            raise
        finally:
            os.close(descriptor)
        self._complete = True

    def _plan(self):
        items, names = [], set()
        for source in self.sources:
            path = self._path(source).rstrip("/")
            name = os.path.basename(path)
            if name in names:
                raise OperationError.at(source, "compress", "two items have this name")
            names.add(name)
            self._walk(items, path, name)
        return items

    def _walk(self, items, path, arcname):
        """Add `path` and everything in it, each folder before what is in it. From a list
        of what is left to read, not a call for each folder: no tree is too deep."""
        pending = [(path, arcname)]
        while pending:
            path, arcname = pending.pop()
            self._check_cancelled()
            try:
                status = os.lstat(path)
                children = sorted(os.listdir(path)) if stat.S_ISDIR(status.st_mode) else ()
            except OSError as error:
                self.errors.append(OperationError(path, "compress", _reason(error)))
                continue
            items.append((path, arcname, status))
            # Last first, so they come off the list in order
            for child in reversed(children):
                pending.append((os.path.join(path, child), f"{arcname}/{child}"))

    def _temp(self, folder):
        """A new file to write the archive in: its path, and a descriptor the archive is
        written through, never the name, which could be made a link meanwhile."""
        while True:
            temp = os.path.join(folder, f"{_TEMP_PREFIX}{secrets.token_hex(4)}{_TEMP_SUFFIX}")
            try:
                descriptor = os.open(temp, _NEW_FILE_FLAGS, 0o666)
            except FileExistsError:
                continue
            except OSError as error:
                raise OperationError.at(self.destination, "compress", _reason(error)) from error
            return temp, descriptor

    def _add(self, writer, path, arcname, status):
        mode = status.st_mode
        if stat.S_ISDIR(mode):
            writer.add(arcname, status)
            return
        if stat.S_ISLNK(mode):
            try:
                link = os.readlink(path)
            except OSError as error:
                self.errors.append(OperationError(path, "compress", _reason(error)))
                return
            writer.add(arcname, status, link=link)
            self._count(files=1)
            return
        if not stat.S_ISREG(mode):
            self.errors.append(
                OperationError(path, "compress", "is a special file, so it was left out")
            )
            return
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError as error:
            self.errors.append(OperationError(path, "compress", _reason(error)))
            return
        try:
            # As it is now, opened: the size written is the size read
            status = os.fstat(descriptor)
            if not stat.S_ISREG(status.st_mode):
                self.errors.append(OperationError(path, "compress", "is no longer a file"))
                return
            writer.add(arcname, status, blocks=self._read(descriptor, status.st_size, path))
        except OSError as error:
            # Half of a file is in the archive: it cannot be finished
            raise OperationError(path, "compress", _reason(error)) from error
        finally:
            os.close(descriptor)
        self._count(files=1, size=status.st_size)

    def _read(self, descriptor, size, path):
        remaining = size
        while remaining:
            self._check_cancelled()
            block = os.read(descriptor, min(_BLOCK, remaining))
            if not block:
                raise OperationError(path, "compress", "changed while it was being compressed")
            remaining -= len(block)
            self._set_progress(bytes_done=self._bytes_done + size - remaining)
            yield block

    def _place(self, temp, name):
        """Give the finished archive its name. Never onto something that is there."""
        source = Gio.File.new_for_path(temp)
        while True:
            target = self.destination.get_child(name)
            try:
                # Not cancellable: a complete archive is put in place, not thrown away
                source.move(target, _PLACE, None, None, None)
            except GLib.Error as error:
                if not has_code(error, Gio.IOErrorEnum.EXISTS):
                    raise OperationError.from_glib(error, target, "compress") from error
                name = self._free_name(name, False, locations.file_type(target))
                continue
            return target


def _is_ours(temp, descriptor):
    """Whether the name `temp` is still the file open as `descriptor`."""
    try:
        named, opened = os.lstat(temp), os.fstat(descriptor)
    except OSError:
        return False
    return (named.st_dev, named.st_ino) == (opened.st_dev, opened.st_ino)


def _discard(temp, descriptor):
    """Remove the temporary file, if what has its name is still it."""
    if not _is_ours(temp, descriptor):
        return
    try:
        os.unlink(temp)
    except OSError:
        pass
