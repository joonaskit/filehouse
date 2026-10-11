import pytest
from ui_helpers import TIMEOUT

from filehouse.qt_models import workers
from filehouse.qt_models.job_model import JobModel
from filehouse.ui.dialogs.prompter import Prompter


@pytest.fixture
def bare_jobs(qtbot):
    """A JobModel nobody answers questions for. Finished jobs stay listed."""
    model = JobModel(keep_finished_ms=3_600_000)
    yield model
    model.cancel_all()
    # Not a blocking wait: the workers need this thread to let go of the interpreter
    qtbot.waitUntil(lambda: workers.active_count() == 0, timeout=TIMEOUT)


@pytest.fixture
def jobs(bare_jobs):
    """A JobModel with a Prompter, as the app sets them up."""
    bare_jobs.prompter = Prompter(bare_jobs, bare_jobs)
    return bare_jobs


@pytest.fixture
def wait_final(qtbot):
    """Wait until the UI thread has seen a job end."""

    def wait(model, job):
        qtbot.waitUntil(lambda: job.id in model._ended, timeout=TIMEOUT)
        return job

    return wait


@pytest.fixture
def wait_question(qtbot):
    """Wait for the next question box and return it."""

    def wait(model):
        qtbot.waitUntil(lambda: model.prompter.current is not None, timeout=TIMEOUT)
        return model.prompter.current

    return wait


@pytest.fixture(autouse=True)
def no_blocking_close(monkeypatch):
    """Closing a window with jobs running asks in a blocking dialog and may quit the app.
    In tests, windows left open are closed by the cleanup: cancel instead, and never quit."""
    from filehouse.ui import main_window

    monkeypatch.setattr(main_window, "ask_about_running_jobs", lambda *_: main_window.CANCEL_ALL)
    monkeypatch.setattr(main_window, "quit_app", lambda: None)


@pytest.fixture(scope="session", autouse=True)
def release_clipboard(qapp):
    """As the app does when it quits: see clipboard.release."""
    from filehouse.ui import clipboard

    yield
    clipboard.release()
