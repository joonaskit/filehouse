"""Extracting an archive and compressing into one, each as a job.

They are jobs like copy and move, with the same states, progress, questions and
errors, but an archive is read and written from start to end, so they do not plan
steps: there is nothing to retry in the middle of a stream.

What keeps them safe:

- Nothing that exists is ever replaced. An extraction writes only inside a new hidden
  folder in the destination, and then makes one new item of it (the archive's only
  top-level item, or the folder named after the archive when it has several); an
  archive is written under a temporary name and put in place when it is complete. A
  name that is taken asks. Where the destination cannot rename, what was made is
  copied into place instead, never onto anything.
- A path from an archive is used only when `archives.safe_parts` accepts it, and every
  folder on the way to a file is opened without following links, so a link from the
  archive cannot be written through.
- Cancel removes the file being written, nothing else.
- Compressing reads its sources and never changes them.
"""

import errno
import os
import secrets
import shutil
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
_COPY = Gio.FileCopyFlags.NOFOLLOW_SYMLINKS | Gio.FileCopyFlags.ALL_METADATA
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
_FATAL_GIO = (Gio.IOErrorEnum.NO_SPACE, Gio.IOErrorEnum.READ_ONLY)


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
        self._total_known = False  # whether `_files_total` counts everything there is
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
        if not cancelled and not self.errors and not self._complete:
            # Unreachable by design, but `done` must never be a guess
            self.errors.append(OperationError("", self.kind.value, "did not finish"))
        done, total, known = self._files_done, self._files_total, self._total_known
        verb = _VERBS[self.kind]
        if cancelled:
            state = JobState.CANCELLED
            if known:
                self.summary = f"Cancelled after {done} of {_files(total)}"
            else:
                self.summary = f"Cancelled after {_files(done)}" if done else "Cancelled"
        elif self.errors:
            state = JobState.FAILED
            if not known:
                # Stopped before the end, where the total would have been known
                self.summary = str(self.errors[0])
                if done:
                    self.summary = f"{verb} {_files(done)}, then stopped"
            elif total > done:
                self.summary = f"{verb} {done} of {_files(total)}, {total - done} left out"
            else:
                self.summary = f"{verb} {done} of {_files(total)}, {len(self.errors)} failed"
        else:
            state = JobState.DONE
            self.summary = f"{verb} {_files(done)}"
        self.progress = replace(
            self.progress,
            current="",
            files_done=done,
            files_total=total,
            bytes_done=self._bytes_done,
        )
        self._set_state(state)


def _files(count):
    return f"{count} file{'' if count == 1 else 's'}"


class ExtractJob(_ArchiveJob):
    """Extract the archive `archive` into the folder `destination`.

    The archive is read once, into a hidden folder in the destination, and what it held
    is put in place at the end, when what is at its top level is known. `prompt` also
    answers `conflicts.Password` and `conflicts.NameTaken`. Without one, a protected
    archive and a taken name both fail the job.
    """

    def __init__(self, archive, destination, *, prompt=None, on_change=None):
        super().__init__(JobKind.EXTRACT, [archive], destination, prompt, on_change)
        self._tops = set()  # the first part of each path that may be used
        self._made = 0  # entries written
        self._seen = 0  # entries looked at
        self._next = 0  # the first entry not finished with, where a new pass starts
        self._unlocked = False  # the password has opened something
        self._reader = None
        self._current = None  # the entry being written
        self._root = None  # the name of what was made, once it is in place
        self._wrap = False
        self._folders = []  # (parts, mode, mtime) to set once everything is inside
        self._umask = 0o022
        self._unplaced = []  # (error, parts): named again once it is known where they went

    def _work(self):
        archive = self.sources[0]
        path, folder = self._path(archive), self._path(self.destination)
        try:
            size = os.stat(path).st_size
        except OSError as error:
            raise OperationError.at(archive, "extract", _reason(error)) from error
        # How far it is: the archive's own bytes read, as nothing else is known before
        # the end without reading it twice
        self._set_progress(bytes_total=size, planned=True)
        self._umask = _umask()
        try:
            base = os.open(folder, _FOLDER_FLAGS & ~os.O_NOFOLLOW)
        except OSError as error:
            raise OperationError.at(self.destination, "extract", _reason(error)) from error
        try:
            temp, work = self._make_temp(base)
            try:
                self._set_state(JobState.RUNNING)
                stopped = None
                try:
                    self._extract_all(path, work)
                except Exception as error:
                    stopped = error
                self._finish_folders(work)
                self._put_in_place(folder, base, temp, work, stopped)
            finally:
                os.close(work)
        finally:
            os.close(base)
        self._complete = True

    def _make_temp(self, base):
        """The hidden folder everything is written in: its name, and a descriptor."""
        while True:
            name = f"{_TEMP_PREFIX}{secrets.token_hex(4)}{_TEMP_SUFFIX}"
            try:
                os.mkdir(name, 0o700, dir_fd=base)
            except FileExistsError:
                continue
            except OSError as error:
                raise OperationError.at(self.destination, "extract", _reason(error)) from error
            return name, os.open(name, _FOLDER_FLAGS, dir_fd=base)

    # Reading

    def _extract_all(self, path, work):
        """Read the archive, asking for its password when an entry needs one. With a
        password the archive is read again, from that entry on."""
        archive = self.sources[0]
        passphrase, asked = None, False
        while True:
            try:
                self._read(path, passphrase, work)
                break
            except archives.NeedsPassword as problem:
                if self._unlocked:
                    raise OperationError.at(
                        archive, "extract", "the password does not open all of it"
                    ) from problem
            reply = self._ask(conflicts.Password(archive, wrong=asked))
            if reply is None:
                raise OperationError.at(archive, "extract", "is protected with a password")
            if reply.answer is not Answer.PASSWORD:
                self._cancel_from_answer()
            passphrase, asked = reply.text, True
        if not self._seen:
            raise OperationError.at(archive, "extract", "is empty")
        self._total_known = True
        self._bytes_done = self.progress.bytes_total

    def _read(self, path, passphrase, work):
        archive = self.sources[0]
        try:
            with archives.reading(path, passphrase) as reader:
                self._reader = reader
                for index, (entry, blocks) in enumerate(reader):
                    if index < self._next:
                        # Written already, before a password was asked for
                        continue
                    self._check_cancelled()
                    if index == self._seen:
                        self._seen += 1
                        self._files_total += entry.kind != archives.FOLDER
                    self._current = entry
                    self._bytes_done = reader.bytes_read
                    self._set_progress(
                        current=entry.name.rstrip("/").rpartition("/")[2],
                        bytes_done=self._bytes_done,
                    )
                    self._extract_one(entry, blocks, work)
                    self._current = None
                    self._next = index + 1
        except archives.Unsupported as problem:
            raise OperationError.at(archive, "extract", str(problem)) from problem
        except archives.ArchiveProblem as problem:
            if not self._seen:
                # Not an archive, or not one libarchive can read
                raise OperationError.at(archive, "extract", str(problem)) from problem
            # The stream is broken: what comes after cannot be read either
            entry, message = self._current, f"the archive is damaged ({problem})"
            if entry is None or entry.parts is None:
                raise OperationError.at(archive, "extract", message) from None
            raise self._inside(entry.parts, message) from None
        finally:
            self._reader = None

    # Writing

    def _inside(self, parts, message):
        """An error about what is written: its path is known once it is in place."""
        error = OperationError("/".join(parts), "extract", message)
        self._unplaced.append((error, parts))
        return error

    def _refuse(self, entry):
        self.errors.append(
            OperationError(entry.name, "extract", "has an unsafe path, so it was not extracted")
        )

    def _extract_one(self, entry, blocks, work):
        if entry.parts is None:
            self._refuse(entry)
            return
        self._tops.add(entry.parts[0])
        try:
            self._write(entry, blocks, work, entry.parts)
        except FileExistsError:
            # The folder is new and ours: only the archive itself made it
            self.errors.append(self._inside(entry.parts, "is in the archive twice"))
        except OperationError as error:
            self.errors.append(error)
        except OSError as error:
            failure = self._inside(entry.parts, _reason(error))
            if error.errno in _FATAL:
                raise failure from error
            self.errors.append(failure)
        else:
            self._made += 1
            if entry.kind != archives.FOLDER:
                self._count(files=1)

    def _write(self, entry, blocks, work, parts):
        name = parts[-1]
        parent = _open_folder(work, parts[:-1], create=True)
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
                self._hardlink(entry, work, parent, name, parts)
            else:
                raise self._inside(parts, "is a special file, so it was not extracted")
        finally:
            os.close(parent)

    def _write_file(self, entry, blocks, parent, name):
        descriptor = os.open(name, _NEW_FILE_FLAGS, 0o600, dir_fd=parent)
        try:
            for block in blocks():
                self._check_cancelled()
                view = memoryview(block)
                while view:
                    view = view[os.write(descriptor, view) :]
                self._bytes_done = self._reader.bytes_read
                self._set_progress(bytes_done=self._bytes_done)
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
            raise
        os.close(descriptor)
        if entry.encrypted:
            self._unlocked = True

    def _hardlink(self, entry, work, parent, name, parts):
        target = archives.safe_parts(entry.link)
        if target is None:
            raise self._inside(parts, "links to an unsafe path, so it was not extracted")
        folder = _open_folder(work, target[:-1], create=False)
        try:
            os.link(target[-1], name, src_dir_fd=folder, dst_dir_fd=parent, follow_symlinks=False)
        finally:
            os.close(folder)

    def _finish_folders(self, work):
        """Folders get their permissions and times last, deepest first, so one that
        is read-only can still be filled. Best effort, as for a copied folder."""
        for parts, mode, mtime in sorted(self._folders, key=lambda item: -len(item[0])):
            try:
                folder = _open_folder(work, parts, create=False)
            except OSError:
                continue
            try:
                if mode:
                    # Always the owner's to open, to move and to remove things from
                    os.fchmod(folder, (mode & 0o777 & ~self._umask) | 0o700)
                if mtime is not None:
                    os.utime(folder, (mtime, mtime))
            except OSError:
                pass
            finally:
                os.close(folder)

    # Putting it in place

    def _put_in_place(self, folder, base, temp, work, stopped):
        """Give the destination one new item: the archive's only top-level item, or the
        hidden folder named after the archive when it has several. When the job was
        stopped, what is complete is given all the same, under a free name."""
        try:
            made = None
            if self._made and (stopped is None or self._files_done):
                made = self._what_was_made(folder, temp, work)
            if made is None:
                # Nothing was written, or only folders before it stopped: no use to anyone
                _discard_folder(base, temp, work)
            else:
                source, name, is_dir = made
                if self._wrap:
                    os.fchmod(work, 0o777 & ~self._umask)
                self.result = self._place(source, name, is_dir, ask=stopped is None)
                # What it held is in place: gone if it was moved, a copy left if copied
                _discard_folder(base, temp, work)
        except BaseException:
            # A taken name nobody chose a new one for: what was extracted is not given
            _discard_folder(base, temp, work)
            raise
        finally:
            named = self._name_errors()
        if stopped is not None:
            raise named.get(id(stopped), stopped)

    def _what_was_made(self, folder, temp, work):
        """(path, name, is_dir) of what to put in place, or None."""
        self._wrap = len(self._tops) != 1
        if self._wrap:
            name = archives.folder_name(self.sources[0].get_basename())
            return os.path.join(folder, temp), name, True
        top = next(iter(self._tops))
        try:
            is_dir = stat.S_ISDIR(os.lstat(top, dir_fd=work).st_mode)
        except OSError:
            return None
        return os.path.join(folder, temp, top), top, is_dir

    def _place(self, path, name, is_dir, ask):
        """Move what was made to `name` in the destination. Never onto something that is
        there: a taken name asks, or with `ask` false, takes a free one."""
        source = Gio.File.new_for_path(path)
        moves = True
        while True:
            target = self.destination.get_child(name)
            try:
                # Not cancellable: what is complete is given, not thrown away
                if moves:
                    _move(source, target)
                else:
                    self._copy_made(source, target, is_dir)
            except GLib.Error as error:
                if moves and has_code(error, Gio.IOErrorEnum.NOT_SUPPORTED):
                    # The destination cannot rename (MTP, some WebDAV): copied instead
                    moves = False
                    continue
                if not has_code(error, Gio.IOErrorEnum.EXISTS):
                    raise OperationError.from_glib(error, target, "extract") from error
                if ask:
                    existing = locations.file_type(target) or Gio.FileType.REGULAR
                    name = self._free_name(name, is_dir, existing)
                else:
                    name = conflicts.free_name(self.destination, name, is_dir)
                continue
            self._root = name
            return target

    def _copy_made(self, source, target, is_dir):
        """Copy what was made to the new `target`, where it cannot be moved. Raises EXISTS
        only for `target` itself. Inside it, what cannot be copied is named and left out,
        and the files counted are the ones copied; no space left stops it."""
        if not is_dir:
            _copy_new(source, target)
            return
        target.make_directory(None)
        self._root, self.result = target.get_basename(), target
        files, folders, pending = 0, [], [(source, target)]
        try:
            while pending:
                folder, copy = pending.pop()
                folders.append((folder, copy))
                try:
                    children = list(
                        folder.enumerate_children(
                            "standard::name,standard::type",
                            Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS,
                            None,
                        )
                    )
                except GLib.Error as error:
                    self.errors.append(OperationError.from_glib(error, copy, "extract"))
                    continue
                for info in children:
                    child, made = folder.get_child(info.get_name()), copy.get_child(info.get_name())
                    try:
                        if info.get_file_type() == Gio.FileType.DIRECTORY:
                            made.make_directory(None)
                            pending.append((child, made))
                        else:
                            _copy_new(child, made)
                            files += 1
                    except GLib.Error as error:
                        failure = OperationError.from_glib(error, made, "extract")
                        if any(has_code(error, code) for code in _FATAL_GIO):
                            raise failure from error
                        self.errors.append(failure)
        finally:
            self._files_done = files
            # Deepest first, after what is in them, as when they were extracted
            for folder, copy in reversed(folders):
                try:
                    folder.copy_attributes(copy, _COPY, None)
                except GLib.Error:
                    pass

    def _shown(self, parts):
        """The path of what an entry became, for messages."""
        folder = self.destination.get_parse_name().rstrip("/")
        if self._root is None:
            return "/".join((folder, *parts))
        if self._wrap:
            return "/".join((folder, self._root, *parts))
        return "/".join((folder, self._root, *parts[1:]))

    def _name_errors(self):
        """Errors about what was written name where it is now. Returns the new error
        for each one, by the id of the old."""
        named = {
            id(error): OperationError(self._shown(parts), error.step, error.message)
            for error, parts in self._unplaced
        }
        self.errors = [named.get(id(error), error) for error in self.errors]
        return named


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
        self._total_known = True
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
            raise OperationError(os.path.join(folder, name), "compress", str(problem)) from problem
        finally:
            # Gone once moved into place; left after a copy, a failure or a cancel
            _discard(temp, descriptor)
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
            if not _is_utf8(arcname):
                # libarchive cannot store it in every format and locale alike: left out,
                # with all that is in it, rather than written as something else
                self.errors.append(
                    OperationError(path, "compress", "its name is not UTF-8, so it was left out")
                )
                continue
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
            if not _is_utf8(link):
                self.errors.append(
                    OperationError(
                        path, "compress", "where it points is not UTF-8, so it was left out"
                    )
                )
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
        moves = True
        while True:
            target = self.destination.get_child(name)
            try:
                # Not cancellable: a complete archive is put in place, not thrown away
                if moves:
                    _move(source, target)
                else:
                    _copy_new(source, target)
            except GLib.Error as error:
                if moves and has_code(error, Gio.IOErrorEnum.NOT_SUPPORTED):
                    # The destination cannot rename (MTP, some WebDAV): copied instead
                    moves = False
                    continue
                if not has_code(error, Gio.IOErrorEnum.EXISTS):
                    raise OperationError.from_glib(error, target, "compress") from error
                name = self._free_name(name, False, locations.file_type(target))
                continue
            return target


def _move(source, target):
    source.move(target, _PLACE, None, None, None)


def _copy_new(source, target):
    """Copy the file or link `source` to `target`, which must not exist. A partial copy is
    removed: nothing else could have made it."""
    try:
        source.copy(target, _COPY, None, None, None)
    except GLib.Error as error:
        if not has_code(error, Gio.IOErrorEnum.EXISTS):
            try:
                target.delete(None)
            except GLib.Error:
                pass
        raise


def _discard_folder(base, name, descriptor):
    """Remove the hidden folder `name` an extraction wrote in, with all that is in it,
    if what has its name is still the folder open as `descriptor`."""
    try:
        named, opened = os.lstat(name, dir_fd=base), os.fstat(descriptor)
    except OSError:
        return
    if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
        return
    # Through descriptors and without following links, below `base`
    shutil.rmtree(name, dir_fd=base, ignore_errors=True)


def _is_utf8(name):
    # A name that is not keeps its bytes as surrogates (surrogateescape)
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


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
