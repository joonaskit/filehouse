"""The preview window that Space opens."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from ui_helpers import TIMEOUT, gfile

from filehouse.qt_models import previews
from filehouse.ui import preview
from filehouse.ui.main_window import MainWindow


def picture(path, width, height, colour="#3366cc"):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(colour))
    assert image.save(str(path))


@pytest.fixture
def folder(tmp_path):
    picture(tmp_path / "a.png", 640, 320)
    picture(tmp_path / "b.png", 100, 400, "#cc3333")
    (tmp_path / "notes.txt").write_text("hello")
    (tmp_path / "sub").mkdir()
    return tmp_path


@pytest.fixture
def window(qtbot, folder, jobs):
    window = MainWindow(gfile(folder), jobs=jobs)
    qtbot.addWidget(window)
    window.resize(1000, 600)
    window.show()
    qtbot.waitUntil(lambda: not window._model.loading, timeout=TIMEOUT)
    return window


def loaded(qtbot, window):
    canvas = window._preview._canvas
    qtbot.waitUntil(lambda: canvas._image is not None, timeout=TIMEOUT)
    return canvas


def test_it_is_closed_until_asked(window):
    assert not window._preview.isVisible()
    assert not window._preview_action.isEnabled()


def test_space_opens_the_preview_of_the_selected_picture(qtbot, window):
    window._view.select_name("a.png")

    qtbot.keyClick(window._view, Qt.Key.Key_Space)

    assert window._preview.isVisible()
    assert window._preview.windowTitle() == "a.png"
    canvas = loaded(qtbot, window)
    assert (canvas._image.width(), canvas._image.height()) == (640, 320)
    assert "640 × 320" in window._preview._details.text()
    assert "PNG" in window._preview._details.text()


def test_space_or_escape_close_it(qtbot, window):
    window._view.select_name("a.png")
    window.toggle_preview()
    assert window._preview.isVisible()

    window.toggle_preview()
    assert not window._preview.isVisible()

    window.toggle_preview()
    qtbot.keyClick(window._preview, Qt.Key.Key_Escape)
    assert not window._preview.isVisible()

    window.toggle_preview()
    qtbot.keyClick(window._preview, Qt.Key.Key_Space)
    assert not window._preview.isVisible()


def test_nothing_selected_opens_nothing(window):
    window.toggle_preview()

    assert not window._preview.isVisible()


def test_the_arrow_keys_move_the_selection_and_the_preview_follows(qtbot, window):
    window._view.select_name("a.png")
    window.toggle_preview()
    loaded(qtbot, window)

    qtbot.keyClick(window._preview, Qt.Key.Key_Down)

    qtbot.waitUntil(lambda: window._preview.windowTitle() == "b.png", timeout=TIMEOUT)
    assert [info.name for info in window._view.selected_infos()] == ["b.png"]
    canvas = loaded(qtbot, window)
    qtbot.waitUntil(lambda: canvas._image.width() == 100, timeout=TIMEOUT)


def test_a_picture_is_shown_whole_and_not_distorted(qtbot, window):
    window._view.select_name("b.png")
    window.toggle_preview()
    canvas = loaded(qtbot, window)
    canvas.resize(500, 300)

    canvas.grab()

    size = canvas._scaled.deviceIndependentSize()
    # 100 x 400 into 500 x 300: as tall as the box
    assert size.height() == pytest.approx(300, abs=2)
    assert size.width() == pytest.approx(75, abs=2)


def test_a_small_picture_is_not_blown_up_past_twice_its_size(qtbot, window, folder):
    picture(folder / "tiny.png", 20, 20)
    qtbot.waitUntil(lambda: window._model.row_for_name("tiny.png") >= 0, timeout=TIMEOUT)
    window._view.select_name("tiny.png")
    window.toggle_preview()
    canvas = loaded(qtbot, window)
    canvas.resize(800, 600)

    canvas.grab()

    assert canvas._scaled.deviceIndependentSize().width() == pytest.approx(40, abs=1)


def test_a_file_without_a_preview_shows_its_icon_and_says_so(qtbot, window):
    window._view.select_name("notes.txt")

    window.toggle_preview()

    assert window._preview.isVisible()
    assert window._preview._canvas._image is None
    assert window._preview._canvas._text == "No preview for this type"
    assert window._preview.windowTitle() == "notes.txt"


def test_a_folder_shows_its_icon(qtbot, window):
    window._view.select_name("sub")

    window.toggle_preview()

    assert window._preview._canvas._text == "Folder"
    assert window._preview._canvas._image is None


def test_several_items_say_how_many(qtbot, window):
    window._view.select_name("a.png")
    window.toggle_preview()
    window._view.select_all()

    assert window._preview._canvas._text == f"{len(window._view.selected_infos())} items selected"


def test_the_preview_goes_when_the_selection_does(qtbot, window):
    window._view.select_name("a.png")
    window.toggle_preview()

    window._view.selection_model().clearSelection()

    assert not window._preview.isVisible()


def test_a_huge_picture_is_loaded_at_a_size_the_screen_can_use(tmp_path, monkeypatch):
    picture(tmp_path / "wide.png", 3000, 1000)
    monkeypatch.setattr(previews, "MAX_SIDE", 1200)

    image = previews.load(str(tmp_path / "wide.png"), "image/png")

    assert (image.width(), image.height()) == (1200, 400)


def test_a_file_that_is_too_large_or_not_a_picture_is_not_previewed(monkeypatch):
    assert previews.can_preview("image/png", 1000)
    assert not previews.can_preview("text/plain", 10)
    monkeypatch.setattr(previews, "MAX_BYTES", 100)
    assert not previews.can_preview("image/png", 101)


def test_a_picture_that_cannot_be_read_says_so(qtbot, window, folder):
    (folder / "broken.png").write_text("not a picture")
    qtbot.waitUntil(lambda: window._model.row_for_name("broken.png") >= 0, timeout=TIMEOUT)
    window._view.select_name("broken.png")

    window.toggle_preview()

    qtbot.waitUntil(lambda: window._preview._canvas._text == "Could not read it", timeout=TIMEOUT)


def test_the_same_file_is_not_loaded_twice(qtbot, window, monkeypatch):
    window._view.select_name("a.png")
    window.toggle_preview()
    loaded(qtbot, window)
    asked = []
    monkeypatch.setattr(window._preview._loader, "request", lambda *args: asked.append(args))

    window._update_state()
    window._update_state()

    assert asked == []


def test_the_keys_that_navigate_are_the_lists(qtbot):
    assert Qt.Key.Key_Up in preview.NAVIGATION_KEYS
    assert Qt.Key.Key_Space not in preview.NAVIGATION_KEYS
