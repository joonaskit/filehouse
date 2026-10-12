"""The preview window that Space opens."""

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
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
    (tmp_path / "notes.txt").write_text("hello\nworld\n")
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02" * 100)
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
    window._view.select_name("blob.bin")

    window.toggle_preview()

    assert window._preview.isVisible()
    assert window._preview._canvas._image is None
    assert window._preview._canvas._text == "No preview for this type"
    assert window._preview.windowTitle() == "blob.bin"


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
    assert not previews.can_preview("application/octet-stream", 10)
    assert not previews.can_preview("video/mp4", 10)
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


# Text


def shown_text(window):
    return window._preview._text.toPlainText()


def test_a_text_file_shows_its_start(qtbot, window):
    window._view.select_name("notes.txt")

    window.toggle_preview()

    qtbot.waitUntil(lambda: shown_text(window) == "hello\nworld\n", timeout=TIMEOUT)
    assert window._preview._pages.currentWidget() is window._preview._text
    assert window._preview._text.isReadOnly()
    # The keys are the window's, so the arrows still move the selection of the list
    assert window._preview._text.focusPolicy() == Qt.FocusPolicy.NoFocus


def test_code_is_text_too(qtbot, window, folder):
    (folder / "run.py").write_text("print('hi')\n")
    qtbot.waitUntil(lambda: window._model.row_for_name("run.py") >= 0, timeout=TIMEOUT)
    window._view.select_name("run.py")

    window.toggle_preview()

    qtbot.waitUntil(lambda: shown_text(window) == "print('hi')\n", timeout=TIMEOUT)


def test_arrows_go_from_a_picture_to_a_text_and_back(qtbot, window):
    window._view.select_name("b.png")
    window.toggle_preview()
    loaded(qtbot, window)

    qtbot.keyClick(window._preview, Qt.Key.Key_Down)
    qtbot.waitUntil(lambda: window._preview.windowTitle() == "blob.bin", timeout=TIMEOUT)
    qtbot.keyClick(window._preview, Qt.Key.Key_Down)
    qtbot.waitUntil(lambda: window._preview.windowTitle() == "notes.txt", timeout=TIMEOUT)
    qtbot.waitUntil(lambda: shown_text(window) == "hello\nworld\n", timeout=TIMEOUT)
    assert window._preview._pages.currentWidget() is window._preview._text

    qtbot.keyClick(window._preview, Qt.Key.Key_Up)
    qtbot.keyClick(window._preview, Qt.Key.Key_Up)
    qtbot.waitUntil(lambda: window._preview.windowTitle() == "b.png", timeout=TIMEOUT)
    loaded(qtbot, window)
    assert window._preview._pages.currentWidget() is window._preview._canvas


def test_a_text_file_that_is_not_text_says_so(qtbot, window, folder):
    # Called text by its name
    (folder / "fake.txt").write_bytes(b"abc\x00def")
    qtbot.waitUntil(lambda: window._model.row_for_name("fake.txt") >= 0, timeout=TIMEOUT)
    window._view.select_name("fake.txt")

    window.toggle_preview()

    qtbot.waitUntil(
        lambda: window._preview._canvas._text == "No preview for this type", timeout=TIMEOUT
    )
    assert window._preview._pages.currentWidget() is window._preview._canvas


def test_a_long_text_is_cut_and_says_so(qtbot, window, folder, monkeypatch):
    monkeypatch.setattr(previews, "TEXT_BYTES", 100)
    (folder / "long.txt").write_text("line of text\n" * 50)
    qtbot.waitUntil(lambda: window._model.row_for_name("long.txt") >= 0, timeout=TIMEOUT)
    window._view.select_name("long.txt")

    window.toggle_preview()

    qtbot.waitUntil(lambda: shown_text(window) != "", timeout=TIMEOUT)
    assert len(shown_text(window)) == 100
    assert "the first" in window._preview._details.text()


@pytest.mark.parametrize(
    "data, expected",
    [
        ("plain".encode(), ("plain", False)),
        ("Åke ja päivä".encode(), ("Åke ja päivä", False)),
        (b"\xef\xbb\xbfwith a mark", ("with a mark", False)),
        ("Åke".encode("utf-16"), ("Åke", False)),
        # Not UTF-8: Western European
        ("Åke".encode("cp1252"), ("Åke", False)),
        (b"", ("", False)),
    ],
)
def test_text_is_read_in_the_encoding_it_has(tmp_path, data, expected):
    path = tmp_path / "t.txt"
    path.write_bytes(data)

    assert previews.read_text(str(path)) == expected


def test_a_cut_inside_a_character_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(previews, "TEXT_BYTES", 5)
    path = tmp_path / "t.txt"
    # "ä" is two bytes: the fifth byte would be the first half of the third one
    path.write_bytes("ääää".encode())

    text, truncated = previews.read_text(str(path))

    assert truncated
    assert text == "ää"


def test_a_file_with_nul_bytes_is_not_text(tmp_path):
    path = tmp_path / "t.txt"
    path.write_bytes(b"abc\x00def")

    assert previews.read_text(str(path)) is None
    assert previews.read_text(str(tmp_path / "missing")) is None


# Zoom and pan


def test_the_wheel_zooms_and_zero_fits_again(qtbot, window):
    window._view.select_name("a.png")
    window.toggle_preview()
    canvas = loaded(qtbot, window)
    canvas.resize(300, 300)

    assert canvas.zoom == 1.0
    qtbot.keyClick(window._preview, Qt.Key.Key_Plus)
    assert canvas.zoom == pytest.approx(1.25)
    qtbot.keyClick(window._preview, Qt.Key.Key_Minus)
    qtbot.keyClick(window._preview, Qt.Key.Key_Minus)
    assert canvas.zoom == pytest.approx(0.8)

    qtbot.keyClick(window._preview, Qt.Key.Key_0)
    assert canvas.zoom == 1.0
    assert canvas._pan.x() == canvas._pan.y() == 0


def test_the_zoom_has_limits(qtbot, window):
    window._view.select_name("a.png")
    window.toggle_preview()
    canvas = loaded(qtbot, window)

    for _ in range(60):
        canvas.zoom_by(1.25)
    assert canvas.zoom == 16.0
    for _ in range(120):
        canvas.zoom_by(0.8)
    assert canvas.zoom == 0.25


def test_a_zoomed_picture_can_be_moved_but_not_out_of_the_canvas(qtbot, window):
    window._view.select_name("a.png")
    window.toggle_preview()
    canvas = loaded(qtbot, window)
    canvas.resize(300, 300)
    canvas.zoom_by(4)
    width, height = canvas._shown_size()

    # Dragged far to the lower right
    qtbot.mousePress(canvas, Qt.MouseButton.LeftButton, pos=canvas.rect().center())
    qtbot.mouseMove(canvas, canvas.rect().center() + QPoint(5000, 5000))
    qtbot.mouseRelease(canvas, Qt.MouseButton.LeftButton)

    assert canvas._pan.x() == pytest.approx((width - canvas.width()) / 2)
    assert canvas._pan.y() == pytest.approx(max(0, (height - canvas.height()) / 2))


def test_a_picture_that_fits_stays_in_the_middle(qtbot, window):
    window._view.select_name("a.png")
    window.toggle_preview()
    canvas = loaded(qtbot, window)
    canvas.resize(2000, 2000)

    qtbot.mousePress(canvas, Qt.MouseButton.LeftButton, pos=canvas.rect().center())
    qtbot.mouseMove(canvas, canvas.rect().center() + QPoint(50, 50))
    qtbot.mouseRelease(canvas, Qt.MouseButton.LeftButton)

    assert canvas._pan.x() == canvas._pan.y() == 0


def test_zooming_keeps_the_point_under_the_pointer(qtbot, window):
    window._view.select_name("a.png")
    window.toggle_preview()
    canvas = loaded(qtbot, window)
    canvas.resize(400, 400)
    point = QPointF(300, 100)
    centre = QPointF(200, 200)
    width, _ = canvas._shown_size()
    # Where in the picture the pointer is, from its middle, as a share of its width
    before = (point - centre - canvas._pan).x() / width

    canvas.zoom_by(2, point)

    width_after, _ = canvas._shown_size()
    assert (point - centre - canvas._pan).x() / width_after == pytest.approx(before, abs=0.01)


def test_a_new_picture_starts_fitted(qtbot, window):
    window._view.select_name("a.png")
    window.toggle_preview()
    canvas = loaded(qtbot, window)
    canvas.zoom_by(2)

    qtbot.keyClick(window._preview, Qt.Key.Key_Down)

    qtbot.waitUntil(lambda: window._preview.windowTitle() == "b.png", timeout=TIMEOUT)
    qtbot.waitUntil(
        lambda: canvas._image is not None and canvas._image.width() == 100, timeout=TIMEOUT
    )
    assert canvas.zoom == 1.0


# Full screen


def test_return_and_a_double_click_toggle_full_screen(qtbot, window):
    window._view.select_name("a.png")
    window.toggle_preview()
    loaded(qtbot, window)

    qtbot.keyClick(window._preview, Qt.Key.Key_Return)
    assert window._preview.isFullScreen()
    qtbot.keyClick(window._preview, Qt.Key.Key_Return)
    assert not window._preview.isFullScreen()

    qtbot.mouseDClick(window._preview._canvas, Qt.MouseButton.LeftButton)
    assert window._preview.isFullScreen()
    qtbot.mouseDClick(window._preview._canvas, Qt.MouseButton.LeftButton)
    assert not window._preview.isFullScreen()


def test_escape_leaves_full_screen_before_it_closes(qtbot, window):
    window._view.select_name("a.png")
    window.toggle_preview()
    window._preview.toggle_full_screen()

    qtbot.keyClick(window._preview, Qt.Key.Key_Escape)
    assert not window._preview.isFullScreen()
    assert window._preview.isVisible()

    qtbot.keyClick(window._preview, Qt.Key.Key_Escape)
    assert not window._preview.isVisible()


def test_closed_in_full_screen_it_opens_as_a_window_again(qtbot, window):
    window._view.select_name("a.png")
    window.toggle_preview()
    window._preview.toggle_full_screen()

    window._preview.hide()
    window.toggle_preview()

    assert window._preview.isVisible()
    assert not window._preview.isFullScreen()
