"""The preview window that Space opens."""

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QImage
from ui_helpers import TIMEOUT, gfile

from filehouse.qt_models import previews
from filehouse.ui import media, preview
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


def select_pictures(window):
    window._view.select_where(lambda info: info.name.endswith(".png"))


def test_several_items_are_shown_one_after_another(qtbot, window):
    window._view.select_name("a.png")
    window.toggle_preview()
    select_pictures(window)
    preview_ = window._preview

    assert preview_.count == 2
    assert preview_.index == 0
    assert preview_.windowTitle() == "a.png"
    assert preview_._details.text().startswith("1 of 2 · ")
    assert not preview_._previous.isHidden() and not preview_._previous.isEnabled()

    qtbot.keyClick(preview_, Qt.Key.Key_Right)

    assert preview_.index == 1
    assert preview_.windowTitle() == "b.png"
    assert preview_._details.text().startswith("2 of 2 · ")
    # The selection is the same: the arrow went through it, not along the list
    assert len(window._view.selected_infos()) == 2
    canvas = loaded(qtbot, window)
    qtbot.waitUntil(lambda: canvas._image.width() == 100, timeout=TIMEOUT)


def test_stepping_through_the_selection_stops_at_the_ends(qtbot, window):
    window._view.select_name("a.png")
    window.toggle_preview()
    select_pictures(window)

    assert not window._preview.step(-1)
    qtbot.keyClick(window._preview, Qt.Key.Key_Up)
    assert window._preview.index == 0
    assert window._preview.step(1)
    assert not window._preview.step(1)
    assert not window._preview._next.isEnabled()
    window._preview._previous.click()
    assert window._preview.index == 0


def test_the_same_selection_keeps_its_place_and_a_new_one_starts_over(qtbot, window):
    window._view.select_name("a.png")
    window.toggle_preview()
    select_pictures(window)
    window._preview.step(1)

    window._update_preview(window._view.selected_infos())
    assert window._preview.index == 1

    window._view.select_all()
    assert window._preview.index == 0


def test_one_file_after_several_has_no_position(qtbot, window):
    window._view.select_name("a.png")
    window.toggle_preview()
    select_pictures(window)
    window._view.select_name("a.png")

    assert window._preview.count == 0
    assert window._preview._previous.isHidden()
    assert " of 2" not in window._preview._details.text()


def test_the_sheet_has_the_buttons_for_several_files(qtbot, window):
    window.resize(480, 600)
    qtbot.waitUntil(lambda: window._narrow, timeout=TIMEOUT)
    window._view.select_name("a.png")
    window.toggle_preview()
    select_pictures(window)

    sheet = window._sheet_preview
    assert sheet.count == 2
    sheet._next.click()
    assert sheet.index == 1


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


# The pages of a document

needs_pdf = pytest.mark.skipif(previews.QPdfDocument is None, reason="Qt has no PDF module")


def document(path, pages):
    from PySide6.QtGui import QPageSize, QPainter, QPdfWriter

    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A5))
    painter = QPainter(writer)
    for number in range(pages):
        if number:
            writer.newPage()
        painter.drawText(200, 400, f"Page {number + 1}")
    painter.end()


def page_shown(qtbot, shown, number):
    qtbot.waitUntil(
        lambda: shown._canvas._image is not None and f"Page {number} of" in shown._details.text(),
        timeout=TIMEOUT,
    )


@needs_pdf
def test_a_document_opens_at_its_first_page_and_says_how_many(qtbot, window, folder):
    document(folder / "paper.pdf", 3)
    qtbot.waitUntil(lambda: window._model.row_for_name("paper.pdf") >= 0, timeout=TIMEOUT)
    window._view.select_name("paper.pdf")

    window.toggle_preview()
    page_shown(qtbot, window._preview, 1)

    assert window._preview.page_count == 3
    assert window._preview._details.text().endswith("Page 1 of 3")
    # On paper: a page is drawn without any
    image = window._preview._canvas._image
    assert image.pixelColor(2, 2) == QColor("white")
    assert image.height() >= 1400


@needs_pdf
def test_the_page_keys_turn_the_pages_and_stop_at_the_ends(qtbot, window, folder):
    document(folder / "paper.pdf", 2)
    qtbot.waitUntil(lambda: window._model.row_for_name("paper.pdf") >= 0, timeout=TIMEOUT)
    window._view.select_name("paper.pdf")
    window.toggle_preview()
    shown = window._preview
    page_shown(qtbot, shown, 1)

    qtbot.keyClick(shown, Qt.Key.Key_PageDown)
    page_shown(qtbot, shown, 2)
    qtbot.keyClick(shown, Qt.Key.Key_PageDown)
    assert shown.page == 1
    # The list behind did not move
    assert [info.name for info in window._view.selected_infos()] == ["paper.pdf"]

    qtbot.keyClick(shown, Qt.Key.Key_PageUp)
    page_shown(qtbot, shown, 1)


@needs_pdf
def test_the_page_keys_are_the_lists_for_anything_but_a_document(qtbot, window):
    window._view.select_name("a.png")
    window.toggle_preview()
    loaded(qtbot, window)
    seen = []
    window._preview.navigate.connect(lambda event: seen.append(event.key()))

    qtbot.keyClick(window._preview, Qt.Key.Key_PageDown)

    assert seen == [Qt.Key.Key_PageDown]
    assert window._preview.page_count == 0


@needs_pdf
def test_each_document_starts_at_its_first_page(qtbot, window, folder):
    document(folder / "one.pdf", 2)
    document(folder / "two.pdf", 2)
    qtbot.waitUntil(lambda: window._model.row_for_name("two.pdf") >= 0, timeout=TIMEOUT)
    window._view.select_name("one.pdf")
    window.toggle_preview()
    page_shown(qtbot, window._preview, 1)
    window._preview.turn_page(1)
    page_shown(qtbot, window._preview, 2)

    window._view.select_name("two.pdf")

    qtbot.waitUntil(lambda: window._preview.windowTitle() == "two.pdf", timeout=TIMEOUT)
    page_shown(qtbot, window._preview, 1)


@needs_pdf
def test_a_document_that_cannot_be_read_says_so(qtbot, window, folder):
    (folder / "broken.pdf").write_bytes(b"%PDF-1.4\nnot really")
    qtbot.waitUntil(lambda: window._model.row_for_name("broken.pdf") >= 0, timeout=TIMEOUT)
    window._view.select_name("broken.pdf")

    window.toggle_preview()

    canvas = window._preview._canvas
    qtbot.waitUntil(lambda: canvas._text == "Could not read it", timeout=TIMEOUT)
    assert window._preview.page_count == 0


# As a sheet in a narrow window

NARROW, WIDE = 560, 1000


def test_in_a_narrow_window_the_preview_is_a_sheet(qtbot, window):
    window.resize(NARROW, 600)
    window._view.select_name("a.png")

    qtbot.keyClick(window._view, Qt.Key.Key_Space)

    assert window._preview_sheet.is_open and not window._preview.isVisible()
    assert window.preview_is_open
    sheet = window._sheet_preview
    qtbot.waitUntil(lambda: sheet._canvas._image is not None, timeout=TIMEOUT)
    # No title bar: the line under the picture names the file
    assert sheet._details.text().startswith("a.png")
    qtbot.waitUntil(lambda: sheet.height() >= 600 * preview.SHEET_SHARE - 30, timeout=TIMEOUT)

    qtbot.keyClick(window._view, Qt.Key.Key_Space)
    assert not window.preview_is_open


def test_the_sheet_follows_the_selection_and_closes_with_escape_or_its_button(qtbot, window):
    window.resize(NARROW, 600)
    window._view.select_name("a.png")
    window.toggle_preview()

    window._view.select_name("notes.txt")
    sheet = window._sheet_preview
    qtbot.waitUntil(lambda: sheet._text.toPlainText() == "hello\nworld\n", timeout=TIMEOUT)

    assert window._close_preview_sheet.isEnabled()
    window._close_preview_sheet.trigger()
    assert not window.preview_is_open and not window._close_preview_sheet.isEnabled()

    window.toggle_preview()
    sheet.close_button.click()
    assert not window.preview_is_open


def test_one_sheet_at_a_time(qtbot, window):
    window.resize(NARROW, 600)
    window._view.select_name("a.png")
    window._show_details.setChecked(True)
    assert window._sheet.is_open

    window.toggle_preview()
    assert window._preview_sheet.is_open and not window._sheet.is_open
    assert not window._show_details.isChecked()

    window._show_details.setChecked(True)
    assert window._sheet.is_open and not window._preview_sheet.is_open


def test_the_preview_changes_form_with_the_window(qtbot, window):
    window._view.select_name("a.png")
    window.toggle_preview()
    assert window._preview.isVisible()

    window.resize(NARROW, 600)
    assert window._preview_sheet.is_open and not window._preview.isVisible()

    window.resize(WIDE, 600)
    assert window._preview.isVisible() and not window._preview_sheet.is_open


def test_a_double_click_on_the_sheet_asks_for_the_full_screen(qtbot, window):
    window.resize(NARROW, 600)
    window._view.select_name("a.png")
    window.toggle_preview()

    window._sheet_preview.full_screen_requested.emit()

    assert window._preview.isVisible() and window._preview.isFullScreen()
    assert window._preview.windowTitle() == "a.png"
    # Leaving the full screen leaves the sheet as it was
    qtbot.keyClick(window._preview, Qt.Key.Key_Escape)
    assert not window._preview.isVisible()
    assert window._preview_sheet.is_open


@needs_pdf
def test_in_the_sheet_the_page_keys_turn_pages_only_for_a_document(qtbot, window, folder):
    document(folder / "paper.pdf", 2)
    qtbot.waitUntil(lambda: window._model.row_for_name("paper.pdf") >= 0, timeout=TIMEOUT)
    window.resize(NARROW, 600)
    window._view.select_name("a.png")
    window.toggle_preview()
    assert not window._next_page.isEnabled()

    window._view.select_name("paper.pdf")
    page_shown(qtbot, window._sheet_preview, 1)
    assert window._next_page.isEnabled() and window._previous_page.isEnabled()

    window._next_page.trigger()
    page_shown(qtbot, window._sheet_preview, 2)

    window.toggle_preview()
    assert not window._next_page.isEnabled()


# Space while a name is being typed at the list


def test_space_in_the_middle_of_a_typed_name_is_part_of_the_name(qtbot, window, folder):
    (folder / "my file.txt").write_text("mine")
    (folder / "my other.txt").write_text("other")
    (folder / "myzz.txt").write_text("zz")
    qtbot.waitUntil(lambda: window._model.row_for_name("myzz.txt") >= 0, timeout=TIMEOUT)
    window._view.select_name("a.png")
    view = window._view.focusProxy()
    view.setFocus()

    qtbot.keyClicks(view, "my")
    assert window._view.is_searching()
    qtbot.keyClick(view, Qt.Key.Key_Space)
    qtbot.keyClicks(view, "o")

    assert not window.preview_is_open
    assert [info.name for info in window._view.selected_infos()] == ["my other.txt"]


def test_space_opens_the_preview_again_once_the_typing_has_stopped(qtbot, window, monkeypatch):
    window._view.select_name("a.png")
    view = window._view.focusProxy()
    view.setFocus()
    qtbot.keyClicks(view, "b")
    assert window._view.is_searching()

    # Longer ago than Qt waits for the next letter of a name
    window._view._typed_at -= 10
    assert not window._view.is_searching()
    qtbot.keyClick(view, Qt.Key.Key_Space)

    assert window.preview_is_open


def test_a_key_with_control_is_not_a_typed_name(qtbot, window):
    window._view.select_name("a.png")
    view = window._view.focusProxy()

    qtbot.keyClick(view, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    qtbot.keyClick(view, Qt.Key.Key_Down)

    assert not window._view.is_searching()


# Video and audio


def write_wave(path, seconds=1):
    import wave

    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(8000)
        f.writeframes(b"\x00\x00" * 8000 * seconds)


@pytest.fixture
def tune(folder):
    write_wave(folder / "tune.wav")


@pytest.mark.skipif(not media.available(), reason="PySide6 without QtMultimedia")
def test_an_audio_file_gets_a_player_that_does_not_start_by_itself(qtbot, tune, window):
    assert window._view.select_name("tune.wav")
    window.toggle_preview()
    player = window._preview.media

    assert player is not None
    assert not player.controls.isHidden()
    assert not player.playing


@pytest.mark.skipif(not media.available(), reason="PySide6 without QtMultimedia")
def test_the_player_goes_when_the_selection_moves_to_something_else(qtbot, tune, window):
    assert window._view.select_name("tune.wav")
    window.toggle_preview()
    player = window._preview.media

    window._view.select_name("notes.txt")

    assert player.controls.isHidden()
    assert player.player.source().isEmpty()


@pytest.mark.skipif(not media.available(), reason="PySide6 without QtMultimedia")
def test_closing_the_preview_stops_the_player(qtbot, tune, window):
    assert window._view.select_name("tune.wav")
    window.toggle_preview()
    player = window._preview.media

    window.toggle_preview()

    assert player.player.source().isEmpty()
    assert not player.playing


def test_the_time_of_a_clip_is_written_short():
    assert media._clock(0) == "0:00"
    assert media._clock(65_000) == "1:05"
    assert media._clock(3_725_000) == "1:02:05"
    assert media._clock(-5) == "0:00"


@pytest.mark.skipif(not media.available(), reason="PySide6 without QtMultimedia")
def test_a_media_file_is_loaded_again_when_the_preview_is_reopened(qtbot, tune, window):
    assert window._view.select_name("tune.wav")
    window.toggle_preview()
    player = window._preview.media
    qtbot.waitUntil(lambda: not player.player.source().isEmpty(), timeout=TIMEOUT)

    window.toggle_preview()
    window.toggle_preview()

    assert not player.player.source().isEmpty()
    assert not player.controls.isHidden()
