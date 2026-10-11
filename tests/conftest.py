import atexit
import os
import shutil
import tempfile
from pathlib import Path

import pytest

# UI tests must not open windows on the developer's desktop
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Tests never touch the real home directory or the real trash. GLib reads these
# once, so they are set before anything imports gi.
_home = tempfile.mkdtemp(prefix="filehouse-test-home-")
atexit.register(shutil.rmtree, _home, ignore_errors=True)
os.environ["HOME"] = _home
os.environ["XDG_DATA_HOME"] = os.path.join(_home, ".local", "share")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_home, ".config")
os.environ["XDG_CACHE_HOME"] = os.path.join(_home, ".cache")


@pytest.fixture
def home_dir():
    """A folder inside the test home, so the home trash (also a test folder) serves it."""
    from gi.repository import GLib

    assert GLib.get_home_dir() == _home
    path = Path(tempfile.mkdtemp(prefix="work-", dir=_home))
    yield path
    shutil.rmtree(path, ignore_errors=True)
