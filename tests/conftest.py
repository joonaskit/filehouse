import atexit
import gc
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

# The desktop's animations are off, as a user may have them: what a test shows or hides
# is shown or hidden at once. The tests of the animations turn them on (`motion.factor`).
# Set where the system's settings are, which no test writes, and not this machine's.
os.environ["XDG_CONFIG_DIRS"] = os.path.join(_home, "etc-xdg")
os.makedirs(os.environ["XDG_CONFIG_DIRS"])
with open(os.path.join(os.environ["XDG_CONFIG_DIRS"], "kdeglobals"), "w") as _file:
    _file.write("[KDE]\nAnimationDurationFactor=0\n")

# What the app says in the user's language (a size is "3 tavut" in Finnish) is tested
# as it is said without one, on whatever machine the tests run
os.environ["LC_ALL"] = "C.UTF-8"
os.environ["LANGUAGE"] = "C"


@pytest.fixture
def home_dir():
    """A folder inside the test home, so the home trash (also a test folder) serves it."""
    from gi.repository import GLib

    assert GLib.get_home_dir() == _home
    path = Path(tempfile.mkdtemp(prefix="work-", dir=_home))
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(autouse=True)
def fresh_thumbnail_cache(monkeypatch):
    """The shared thumbnail cache lies under XDG_CACHE_HOME: each test gets its own, so
    what one made (a list, the details pane) is not found by the next."""
    path = tempfile.mkdtemp(prefix="cache-", dir=_home)
    monkeypatch.setenv("XDG_CACHE_HOME", path)
    yield
    shutil.rmtree(path, ignore_errors=True)


# Python's garbage collector runs in whichever thread allocates enough, and a Qt object
# whose wrapper it collects in a worker thread (a thumbnail task, a listing) aborts the
# process. Collecting is for the main thread, after each test.
gc.disable()


@pytest.fixture(autouse=True)
def collect_garbage_on_the_main_thread():
    yield
    gc.collect()
