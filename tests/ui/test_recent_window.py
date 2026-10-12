"""What the window tells the lists of recent files, and the switch that stops it."""

import pytest
from ui_helpers import TIMEOUT, gfile

from filehouse.integration import desktop
from filehouse.ui import settings
from filehouse.ui.main_window import MainWindow


class FakeRecent:
    def __init__(self):
        self.added = []

    def add(self, location, content_type=None):
        self.added.append((location.get_basename(), content_type))
        return True


@pytest.fixture
def opened(monkeypatch):
    """The apps that would have been started: nothing is, in a test."""
    calls = []
    monkeypatch.setattr(desktop, "open_default", lambda target, _error: calls.append(target))
    monkeypatch.setattr(desktop, "open_with", lambda app, target, _error: calls.append(target))
    return calls


@pytest.fixture
def window(qtbot, tmp_path, jobs):
    (tmp_path / "report.pdf").write_text("x")
    (tmp_path / "folder").mkdir()
    recent = FakeRecent()
    window = MainWindow(gfile(tmp_path), jobs=jobs, recent=recent)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window._model.loading, timeout=TIMEOUT)
    window.recent = recent
    return window


def info(window, name):
    return window._model.info(window._model.row_for_name(name))


def test_an_opened_file_is_told_with_its_type(window, opened):
    window._on_item_activated(info(window, "report.pdf"))

    assert window.recent.added == [("report.pdf", "application/pdf")]
    assert [target.get_basename() for target in opened] == ["report.pdf"]


def test_open_with_tells_it_too(window, opened):
    class App:
        name = "Viewer"

    target = gfile(window._location.get_path() + "/report.pdf")
    window._open_with(App(), target, "application/pdf")

    assert window.recent.added == [("report.pdf", "application/pdf")]
    assert [t.get_basename() for t in opened] == ["report.pdf"]


def test_a_folder_that_is_opened_is_not_told(window, opened):
    window._on_item_activated(info(window, "folder"))

    assert window.recent.added == []
    assert opened == []


def test_a_window_without_a_recorder_still_opens_files(qtbot, tmp_path, jobs, opened):
    (tmp_path / "report.pdf").write_text("x")
    window = MainWindow(gfile(tmp_path), jobs=jobs)
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: not window._model.loading, timeout=TIMEOUT)

    window._on_item_activated(window._model.info(window._model.row_for_name("report.pdf")))

    assert [target.get_basename() for target in opened] == ["report.pdf"]


def test_the_switch_is_in_the_menu_of_the_folder_and_is_global(qtbot, window):
    assert settings.remember_opened()
    assert not window._private.isChecked()
    menu = window._menu_for(None)
    assert window._private in menu.actions()

    window._private.trigger()

    assert not settings.remember_opened()
    # A window opened now starts with it on
    other = MainWindow(gfile(window._location.get_path()), jobs=window._jobs)
    qtbot.addWidget(other)
    assert other._private.isChecked()

    window._private.trigger()

    assert settings.remember_opened()


def test_the_switch_keeps_the_file_out_of_the_lists(qtbot, tmp_path, jobs, opened):
    from filehouse.integration.recent import RecentFiles

    (tmp_path / "report.pdf").write_text("x")
    written = []
    recent = RecentFiles(
        settings.remember_opened, app_id="filehouse", path=str(tmp_path / "x.xbel")
    )
    recent._writer.start = lambda task: written.append(task)
    window = MainWindow(gfile(tmp_path), jobs=jobs, recent=recent)
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: not window._model.loading, timeout=TIMEOUT)
    report = window._model.info(window._model.row_for_name("report.pdf"))
    recent._plasma = lambda _app: True

    window._private.trigger()
    window._on_item_activated(report)
    assert written == []

    window._private.trigger()
    window._on_item_activated(report)
    assert len(written) == 1
