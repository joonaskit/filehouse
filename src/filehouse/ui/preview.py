"""A larger look at the selected file, opened with Space as in Quick Look on the Mac.

The window follows the selection: the arrow keys go on moving it in the list, and
the preview shows what is selected now. Space closes it; Escape leaves full screen
first. A picture can be zoomed with the wheel or + and -, moved by dragging, and put
back to fit with 0. Return or a double click make the window full screen. A document
is shown a page at a time, and Page Up and Page Down turn its pages. A video or an
audio file is played with the controls under it (`ui/media.py`). With several
files selected it shows one of them, and the arrow keys (or the two buttons under it)
go through the selection one after another.

In a narrow window the same preview is a sheet at the bottom: the form made with
`sheet=True`, which is a part of the main window and takes no keys of its own.
"""

from PySide6.QtCore import QMimeDatabase, QPoint, QPointF, QRect, QRectF, QSize, QSizeF, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from filehouse.integration import style
from filehouse.qt_models import dates, previews
from filehouse.qt_models.icons import icon_for_content_type
from filehouse.ui import media

# How much of the main window's size the preview opens with
_SHARE = 0.8
# A small picture is not blown up past this when it is fitted
_MAX_ENLARGEMENT = 2.0
# How far a picture can be zoomed, as a multiple of how it fits
_MIN_ZOOM, _MAX_ZOOM = 0.25, 16.0
# What one step of + and -, or one notch of the wheel, does
_ZOOM_STEP = 1.25
_ICON = 128
# The sheet of a narrow window: this much of the window's height
SHEET_SHARE = 0.45
_SHEET_ICON = 64
# Keys that go on moving the selection of the list behind
NAVIGATION_KEYS = (
    Qt.Key.Key_Up,
    Qt.Key.Key_Down,
    Qt.Key.Key_Left,
    Qt.Key.Key_Right,
    Qt.Key.Key_Home,
    Qt.Key.Key_End,
    Qt.Key.Key_PageUp,
    Qt.Key.Key_PageDown,
)
_STEP_KEYS = (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right)
_PAGE_KEYS = (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown)
_ZOOM_IN_KEYS = (Qt.Key.Key_Plus, Qt.Key.Key_Equal)
_ZOOM_OUT_KEYS = (Qt.Key.Key_Minus, Qt.Key.Key_Underscore)
_FULL_SCREEN_KEYS = (Qt.Key.Key_Return, Qt.Key.Key_Enter)


class _Canvas(QWidget):
    """The picture, as large as fits and never distorted, or an icon with a line of text.
    A picture can be zoomed and moved."""

    double_clicked = Signal()

    def __init__(self, icon_side=_ICON):
        super().__init__()
        self._icon_side = icon_side
        self._image = None
        self._scaled = None  # the picture fitted to the canvas, made once per size
        self._icon = None
        self._text = ""
        self._zoom = 1.0
        self._pan = QPointF(0, 0)  # from the middle, in pixels
        self._drag_from = None
        self.setMinimumSize(style.px(240), style.px(180))

    @property
    def zoom(self):
        return self._zoom

    def show_image(self, image):
        self._image = image
        self._icon = None
        self._scaled = None
        self.fit()

    def show_icon(self, content_type, text):
        self._image = None
        self._icon = icon_for_content_type(content_type)
        self._text = text
        self.fit()

    def clear(self):
        self._image = None
        self._icon = None
        self._text = ""
        self.fit()

    # Zoom and pan

    def fit(self):
        """As large as fits, in the middle."""
        self._zoom = 1.0
        self._pan = QPointF(0, 0)
        self._drag_from = None
        self._update_cursor()
        self.update()

    def zoom_by(self, factor, about=None):
        """Zoom by `factor`, keeping the point `about` (in canvas pixels, the middle by
        default) where it is."""
        if self._image is None:
            return
        zoom = min(_MAX_ZOOM, max(_MIN_ZOOM, self._zoom * factor))
        if zoom == self._zoom:
            return
        centre = QPointF(self.width() / 2, self.height() / 2)
        offset = (QPointF(about) if about is not None else centre) - centre
        self._pan = offset - (offset - self._pan) * (zoom / self._zoom)
        self._zoom = zoom
        self._pan = self._clamped(self._pan)
        self._update_cursor()
        self.update()

    def _fitted_size(self):
        """The size of the picture when it fits, in canvas pixels."""
        image = self._image
        ratio = self.devicePixelRatioF()
        box = QSize(round(self.width() * ratio), round(self.height() * ratio))
        if image.width() > box.width() or image.height() > box.height():
            size = image.size().scaled(box, Qt.AspectRatioMode.KeepAspectRatio)
        else:
            # Up only a little, and only for something small
            limit = QSize(
                round(image.width() * _MAX_ENLARGEMENT), round(image.height() * _MAX_ENLARGEMENT)
            )
            size = image.size().scaled(box.boundedTo(limit), Qt.AspectRatioMode.KeepAspectRatio)
        return QSizeF(size.width() / ratio, size.height() / ratio)

    def _shown_size(self):
        fitted = self._fitted_size()
        return (fitted.width() * self._zoom, fitted.height() * self._zoom)

    def _clamped(self, pan):
        """A picture larger than the canvas can be moved until its edge is at the canvas's;
        a smaller one stays in the middle."""
        width, height = self._shown_size()
        limit_x = max(0.0, (width - self.width()) / 2)
        limit_y = max(0.0, (height - self.height()) / 2)
        return QPointF(max(-limit_x, min(limit_x, pan.x())), max(-limit_y, min(limit_y, pan.y())))

    def _can_move(self):
        width, height = self._shown_size() if self._image is not None else (0, 0)
        return width > self.width() or height > self.height()

    def _update_cursor(self):
        if self._drag_from is not None:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif self._image is not None and self._can_move():
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.unsetCursor()

    def wheelEvent(self, event):
        steps = event.angleDelta().y() / 120
        if steps and self._image is not None:
            self.zoom_by(_ZOOM_STEP**steps, event.position())
            event.accept()
        else:
            super().wheelEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._can_move():
            self._drag_from = event.position() - self._pan
            self._update_cursor()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_from is not None:
            self._pan = self._clamped(event.position() - self._drag_from)
            self.update()

    def mouseReleaseEvent(self, event):
        self._drag_from = None
        self._update_cursor()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.double_clicked.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._scaled = None
        if self._image is not None:
            self._pan = self._clamped(self._pan)
            self._update_cursor()

    # Painting

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self._image is not None:
            self._paint_image(painter)
        elif self._icon is not None:
            self._paint_icon(painter)

    def _paint_image(self, painter):
        ratio = self.devicePixelRatioF()
        width, height = self._shown_size()
        left = (self.width() - width) / 2 + self._pan.x()
        top = (self.height() - height) / 2 + self._pan.y()
        if self._zoom == 1.0:
            # Fitted: made once with the smooth filter, which a large picture shrunk by
            # the painter would not get
            if self._scaled is None:
                fitted = self._fitted_size()
                self._scaled = self._image.scaled(
                    round(fitted.width() * ratio),
                    round(fitted.height() * ratio),
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._scaled.setDevicePixelRatio(ratio)
            painter.drawImage(QPoint(round(left), round(top)), self._scaled)
        else:
            painter.drawImage(QRectF(left, top, width, height), self._image)

    def _paint_icon(self, painter):
        side = style.px(self._icon_side)
        top = (self.height() - side) // 2 - style.px(16)
        pixmap = self._icon.pixmap(QSize(side, side))
        painter.drawPixmap((self.width() - side) // 2, top, pixmap)
        painter.setPen(self.palette().color(self.palette().ColorRole.PlaceholderText))
        painter.drawText(
            QRect(0, top + side + style.px(12), self.width(), style.px(40)),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            self._text,
        )


class PreviewWindow(QWidget):
    # A key that moves the selection, for the list to take
    navigate = Signal(object)
    # How many pages what is shown has: 0 for anything but a document
    pages_changed = Signal(int)
    # Of the sheet: its close button, and a double click, which asks for the full screen
    close_requested = Signal()
    full_screen_requested = Signal()
    # Of the window: it was shown or hidden
    shown_changed = Signal(bool)

    def __init__(self, parent, sheet=False):
        """`sheet` is the form for a narrow window: a part of the window it is in, with
        a button to close it."""
        if sheet:
            super().__init__(parent)
        else:
            super().__init__(parent, Qt.WindowType.Tool)
            self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
            self.setWindowTitle("Preview")
        self._sheet = sheet
        # Opened from the sheet for the full screen only: leaving that closes it
        self.only_full_screen = False
        self._height = style.px(240)
        self._loader = previews.PreviewLoader(self)
        self._loader.ready.connect(self._on_loaded)
        self._loader.text_ready.connect(self._on_text)
        self._loader.page_ready.connect(self._on_page)
        self._token = 0
        self._shown = None  # what was last asked for, so the same again is not loaded twice
        self._request = None  # the document shown, to ask for another page of it
        self._page = 0
        self._page_count = 0
        self._description = ""
        self._media_type = ""
        self._media = None  # made when the first video or audio file is shown
        self._items = []  # the selection when it is several files: FileInfos
        self._path_of = None
        self._items_key = None
        self._index = 0

        self._canvas = _Canvas(_SHEET_ICON if sheet else _ICON)
        if sheet:
            self._canvas.setMinimumSize(style.px(120), style.px(80))
            self._canvas.double_clicked.connect(self.full_screen_requested)
        else:
            self._canvas.double_clicked.connect(self.toggle_full_screen)
        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._text.setFont(style.fixed_font())
        # The keys are the window's: they move the selection of the list
        self._text.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._text.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        self._text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._pages = QStackedWidget()
        self._pages.addWidget(self._canvas)
        self._pages.addWidget(self._text)
        self._details = QLabel()
        self._details.setObjectName("secondary")
        self._details.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._details.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._previous = self._step_button("go-previous-symbolic", "go-previous", "Previous file")
        self._previous.clicked.connect(lambda: self.step(-1))
        self._next = self._step_button("go-next-symbolic", "go-next", "Next file")
        self._next.clicked.connect(lambda: self.step(1))
        layout = QVBoxLayout(self)
        self._layout = layout
        margin = style.px(8 if sheet else 12)
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.setSpacing(style.px(8))
        layout.addWidget(self._pages, 1)
        self.close_button = None
        footer = QHBoxLayout()
        footer.addWidget(self._previous)
        footer.addWidget(self._details, 1)
        footer.addWidget(self._next)
        if sheet:
            self.setObjectName("card")
            self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            self.close_button = QToolButton()
            self.close_button.setIcon(style.icon("window-close-symbolic", "window-close"))
            self.close_button.setToolTip("Close")
            self.close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.close_button.clicked.connect(self.close_requested)
            footer.addWidget(self.close_button)
        layout.addLayout(footer)

    def _step_button(self, name, fallback, tip):
        button = QToolButton()
        button.setIcon(style.icon(name, fallback))
        button.setToolTip(tip)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setAutoRaise(True)
        button.hide()
        return button

    def retheme(self):
        if self._media is not None:
            self._media.retheme()
        self._previous.setIcon(style.icon("go-previous-symbolic", "go-previous"))
        self._next.setIcon(style.icon("go-next-symbolic", "go-next"))
        if self.close_button is not None:
            self.close_button.setIcon(style.icon("window-close-symbolic", "window-close"))

    # The sheet's size: a share of the window it is in

    def set_sheet_height(self, height):
        if height != self._height:
            self._height = height
            self.updateGeometry()

    def sizeHint(self):
        hint = super().sizeHint()
        return QSize(hint.width(), self._height) if self._sheet else hint

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        return QSize(hint.width(), min(hint.height(), self._height)) if self._sheet else hint

    def open_over(self, window):
        """Show it in the middle of `window`, at its size the first time."""
        if not self.isVisible():
            self.resize(round(window.width() * _SHARE), round(window.height() * _SHARE))
        self.show()
        self.raise_()
        self.activateWindow()

    def toggle_full_screen(self):
        if self._sheet:
            return
        if self.isFullScreen() and self.only_full_screen:
            self.hide()
        elif self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    # What it shows

    @property
    def index(self):
        """Which of the several files selected is shown, counted from 0."""
        return self._index

    @property
    def count(self):
        """How many files are selected: 0 or 1 when it shows a single one."""
        return len(self._items)

    def show_items(self, infos, path_of):
        """The selection, a list of FileInfos; `path_of(info)` is where one is on this
        computer (None when it isn't). One file is shown as it is; several are shown one
        at a time, starting with the first when the selection is a new one."""
        if len(infos) < 2:
            self._items = []
            self._items_key = None
            self._index = 0
            self._show_buttons()
            self.show_item(infos[0], path_of(infos[0]))
            return
        key = tuple((i.name, i.modified, i.size) for i in infos)
        if key != self._items_key:
            self._items_key = key
            self._index = 0
        self._items = list(infos)
        self._path_of = path_of
        self._show_current()

    def step(self, by):
        """Go `by` files on in the selection, or back; it stops at the ends. Whether it
        moved."""
        index = max(0, min(len(self._items) - 1, self._index + by))
        if len(self._items) < 2 or index == self._index:
            return False
        self._index = index
        self._show_current()
        return True

    def _show_current(self):
        info = self._items[self._index]
        self._show_buttons()
        self.show_item(info, self._path_of(info))

    def _show_buttons(self):
        several = len(self._items) > 1
        self._previous.setVisible(several)
        self._next.setVisible(several)
        self._previous.setEnabled(several and self._index > 0)
        self._next.setEnabled(several and self._index < len(self._items) - 1)

    def show_item(self, info, path):
        """The file with this FileInfo, at `path` (None when it isn't on this computer)."""
        position = f"{self._index + 1} of {len(self._items)}" if len(self._items) > 1 else ""
        key = (path, info.modified, info.size, position)
        if key == self._shown:
            return
        self._shown = key
        self._token += 1
        self._set_pages(None)
        self._stop_media()
        if not self._sheet:
            self.setWindowTitle(info.display_name)
        kind = QMimeDatabase().mimeTypeForName(info.content_type).comment()
        self._description = self._describe(info, kind, position)
        self._details.setText(self._description)
        if info.is_dir:
            self._show_icon(info.content_type, "Folder")
        elif path is not None and media.can_play(info.content_type):
            self._play(info.content_type, path)
        elif path is not None and previews.can_preview(info.content_type, info.size):
            # The type's icon until it is in
            self._show_icon(info.content_type, "Loading…")
            if previews.has_pages(info.content_type):
                self._request = (path, info.content_type, info.size)
            self._loader.request((self._token, info.name), path, info.content_type, info.size)
        else:
            why = "Not on this computer" if path is None else "No preview for this type"
            self._show_icon(info.content_type, why)

    def show_message(self, text):
        """For something that is not a file."""
        self._items = []
        self._items_key = None
        self._index = 0
        self._show_buttons()
        self._shown = None
        self._token += 1
        self._set_pages(None)
        self._stop_media()
        if not self._sheet:
            self.setWindowTitle("Preview")
        self._description = ""
        self._details.setText("")
        self._show_icon("application/octet-stream", text)

    # Video and audio

    @property
    def media(self):
        """The player, or None until a video or an audio file has been shown."""
        return self._media

    def _play(self, content_type, path):
        if self._media is None:
            self._media = media.Media(self)
            self._pages.addWidget(self._media.video)
            # Under the picture, over the line that describes the file
            self._layout.insertWidget(1, self._media.controls)
            self._media.ready.connect(self._on_media_ready)
            self._media.failed.connect(self._on_media_failed)
            if self._sheet:
                self._media.video.double_clicked.connect(self.full_screen_requested)
            else:
                self._media.video.double_clicked.connect(self.toggle_full_screen)
        self._media_type = content_type
        self._show_icon(content_type, "Loading…")
        self._media.controls.show()
        self._media.load(path)

    def stop(self):
        """Stop what plays, and let go of the file. What was shown is loaded again when
        it is asked for next, because the player has nothing now."""
        if self._media is not None and not self._media.controls.isHidden():
            self._shown = None
        self._stop_media()

    def _stop_media(self):
        if self._media is not None:
            self._media.stop()
            self._media.controls.hide()

    def _on_media_ready(self, has_video):
        if has_video:
            self._pages.setCurrentWidget(self._media.video)
        else:
            self._show_icon(self._media_type, "")

    def _on_media_failed(self):
        self._stop_media()
        self._show_icon(self._media_type, "Could not play it")

    def _show_icon(self, content_type, text):
        self._canvas.show_icon(content_type, text)
        self._pages.setCurrentWidget(self._canvas)

    def _describe(self, info, kind, position=""):
        if self._sheet:
            # The sheet has no title bar to say which file it is
            kind = info.display_name
        size = self.locale().formattedDataSize(info.size) if not info.is_dir else ""
        modified = dates.friendly(info.modified, self.locale())
        return " · ".join(part for part in (position, kind, size, modified) if part)

    def _on_loaded(self, token, image):
        if token[0] != self._token:
            return
        if image.isNull():
            self._show_icon("application/octet-stream", "Could not read it")
            return
        self._canvas.show_image(image)
        self._pages.setCurrentWidget(self._canvas)
        self._details.setText(f"{self._details.text()} · {image.width()} × {image.height()}")

    # The pages of a document

    @property
    def page(self):
        """The page shown, counted from 0."""
        return self._page

    @property
    def page_count(self):
        return self._page_count

    def turn_page(self, by):
        """Go `by` pages on, or back. Whether there was a page to go to."""
        page = max(0, min(self._page_count - 1, self._page + by))
        if self._request is None or page == self._page:
            return False
        self._page = page
        self._token += 1
        self._loader.request((self._token, ""), *self._request, page)
        self._show_page_number()
        return True

    def _set_pages(self, request, page=0, count=0):
        self._request = request
        self._page = page
        if count != self._page_count:
            self._page_count = count
            self.pages_changed.emit(count)

    def _show_page_number(self):
        self._details.setText(f"{self._description} · Page {self._page + 1} of {self._page_count}")

    def _on_page(self, token, image, page, count):
        if token[0] != self._token:
            return
        if image.isNull():
            self._set_pages(None)
            self._show_icon("application/pdf", "Could not read it")
            return
        self._set_pages(self._request, page, count)
        self._canvas.show_image(image)
        self._pages.setCurrentWidget(self._canvas)
        self._show_page_number()

    def _on_text(self, token, text, truncated):
        if token[0] != self._token:
            return
        if text is None:
            # It said it was text, and is not
            self._show_icon("application/octet-stream", "No preview for this type")
            return
        self._text.setPlainText(text)
        self._pages.setCurrentWidget(self._text)
        if truncated:
            limit = self.locale().formattedDataSize(previews.TEXT_BYTES)
            self._details.setText(f"{self._details.text()} · the first {limit}")

    # Keys

    def keyPressEvent(self, event):
        key = event.key()
        if key == Qt.Key.Key_Space:
            self.hide()
        elif key == Qt.Key.Key_Escape:
            if self.isFullScreen() and not self.only_full_screen:
                self.showNormal()
            else:
                self.hide()
        elif key in _FULL_SCREEN_KEYS:
            self.toggle_full_screen()
        elif key in _ZOOM_IN_KEYS:
            self._canvas.zoom_by(_ZOOM_STEP)
        elif key in _ZOOM_OUT_KEYS:
            self._canvas.zoom_by(1 / _ZOOM_STEP)
        elif key == Qt.Key.Key_0:
            self._canvas.fit()
        elif key in _PAGE_KEYS and self._page_count > 1:
            # Of the document, not of the list: at its end the page stays
            self.turn_page(1 if key == Qt.Key.Key_PageDown else -1)
        elif len(self._items) > 1 and key in _STEP_KEYS:
            # Through the selection, not along the list
            self.step(1 if key in (Qt.Key.Key_Right, Qt.Key.Key_Down) else -1)
        elif key in NAVIGATION_KEYS:
            self.navigate.emit(event)
        else:
            super().keyPressEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._sheet:
            self.shown_changed.emit(True)

    def hideEvent(self, event):
        super().hideEvent(event)
        self.stop()
        if not self._sheet:
            self.shown_changed.emit(False)
        self.only_full_screen = False
        # Closed in full screen, it opens the next time as a window
        if self.isFullScreen():
            self.setWindowState(self.windowState() & ~Qt.WindowState.WindowFullScreen)
