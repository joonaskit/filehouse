"""Small tools for the operations tests."""

import os
from pathlib import Path

from gi.repository import Gio, GLib

from filehouse.core.operations import Job, JobState


def not_supported(*_args, **_kwargs):
    """Stands in for a Gio call on a file system that can't do it."""
    raise GLib.Error.new_literal(
        Gio.io_error_quark(), "Operation not supported", Gio.IOErrorEnum.NOT_SUPPORTED
    )


def gfile(path):
    return Gio.File.new_for_path(str(path))


def make_tree(root, spec):
    """Create files (str content), links (("link", target)) and folders (dict) under root."""
    root.mkdir(parents=True, exist_ok=True)
    for name, value in spec.items():
        path = root / name
        if isinstance(value, dict):
            make_tree(path, value)
        elif isinstance(value, tuple):
            path.symlink_to(value[1])
        else:
            path.write_text(value)
    return root


def read_tree(root):
    """The inverse of make_tree. A missing root reads as None."""
    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        return None
    result = {}
    for path in root.iterdir():
        if path.is_symlink():
            result[path.name] = ("link", os.readlink(path))
        elif path.is_dir():
            result[path.name] = read_tree(path)
        else:
            result[path.name] = path.read_text()
    return result


class Recorder:
    """An on_change callback that remembers every state and progress it saw."""

    def __init__(self, hook=None):
        self.states = []
        self.progress = []
        self._hook = hook

    def __call__(self, job):
        if not self.states or self.states[-1] is not job.state:
            self.states.append(job.state)
        self.progress.append(job.progress)
        if self._hook is not None:
            self._hook(job)


def run_job(kind, sources, destination=None, prompt=None, hook=None):
    """Run a job to its end on this thread. Returns it with `.seen`, a Recorder."""
    seen = Recorder(hook)
    job = Job(
        kind,
        [gfile(source) for source in sources],
        gfile(destination) if destination is not None else None,
        prompt=prompt,
        on_change=seen,
    )
    job.seen = seen
    job.run()
    assert job.state.is_final
    assert seen.states[-1] is job.state
    return job


def leftovers(*roots):
    """Temporary files the engine failed to clean up."""
    return [str(path) for root in roots for path in Path(root).rglob(".filehouse-*")]


def never_done(job):
    return JobState.DONE not in job.seen.states
