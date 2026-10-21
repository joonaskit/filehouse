"""What the window tells the lists of recent files, and the switch that stops it."""

import pytest
from PySide6.QtCore import Qt
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
    window.open_entry(info(window, "report.pdf"))

    assert window.recent.added == [("report.pdf", "application/pdf")]
    assert [target.get_basename() for target in opened] == ["report.pdf"]


def test_open_with_tells_it_too(window, opened):
    class App:
        name = "Viewer"

    target = gfile(window._location.get_path() + "/report.pdf")
    window.open_with(App(), target, "application/pdf")

    assert window.recent.added == [("report.pdf", "application/pdf")]
    assert [t.get_basename() for t in opened] == ["report.pdf"]


def test_a_folder_that_is_opened_is_not_told(window, opened):
    window.open_entry(info(window, "folder"))

    assert window.recent.added == []
    assert opened == []


def test_a_window_without_a_recorder_still_opens_files(qtbot, tmp_path, jobs, opened):
    (tmp_path / "report.pdf").write_text("x")
    window = MainWindow(gfile(tmp_path), jobs=jobs)
    qtbot.addWidget(window)
    qtbot.waitUntil(lambda: not window._model.loading, timeout=TIMEOUT)

    window.open_entry(window._model.info(window._model.row_for_name("report.pdf")))

    assert [target.get_basename() for target in opened] == ["report.pdf"]


def test_the_switch_is_in_the_menu_of_the_folder_and_is_global(qtbot, window):
    assert settings.remember_opened()
    assert not window._actions.private.isChecked()
    menu = window._menus.for_entry(None)
    assert window._actions.private in menu.actions()

    window._actions.private.trigger()

    assert not settings.remember_opened()
    # A window opened now starts with it on
    other = MainWindow(gfile(window._location.get_path()), jobs=window._jobs)
    qtbot.addWidget(other)
    assert other._actions.private.isChecked()

    window._actions.private.trigger()

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

    window._actions.private.trigger()
    window.open_entry(report)
    assert written == []

    window._actions.private.trigger()
    window.open_entry(report)
    assert len(written) == 1


# Feedback


def test_turning_it_on_says_so_and_shows_a_sign(qtbot, window):
    assert not window._private_button.isVisibleTo(window)

    window._actions.private.trigger()

    assert "not remembered" in window.statusBar().currentMessage()
    assert window._private_button.isVisibleTo(window)
    assert window._actions.private.isChecked()

    window._actions.private.trigger()

    assert "remembered again" in window.statusBar().currentMessage()
    assert not window._private_button.isVisibleTo(window)


def test_the_sign_ends_it_when_clicked(qtbot, window):
    window._actions.private.trigger()

    qtbot.mouseClick(window._private_button, Qt.MouseButton.LeftButton)

    assert settings.remember_opened()
    assert not window._actions.private.isChecked()
    assert not window._private_button.isVisibleTo(window)


def test_every_window_follows_the_one_setting(qtbot, window, tmp_path, jobs):
    other = MainWindow(gfile(tmp_path), jobs=jobs)
    qtbot.addWidget(other)
    other.show()

    window._actions.private.trigger()

    assert other._actions.private.isChecked()
    assert other._private_button.isVisibleTo(other)

    other._private_button.click()

    assert not window._actions.private.isChecked()
    assert not window._private_button.isVisibleTo(window)


def test_a_window_opened_in_private_mode_shows_the_sign(qtbot, tmp_path, jobs):
    settings.set_remember_opened(False)

    window = MainWindow(gfile(tmp_path), jobs=jobs)
    qtbot.addWidget(window)
    window.show()

    assert window._actions.private.isChecked()
    assert window._private_button.isVisibleTo(window)


def test_what_is_switched_on_shows_in_the_menus():
    from filehouse.integration import style

    assert "QMenu::item:checked" in style._stylesheet()
