"""A larger look at the selected file, opened with Space as in Quick Look on the Mac.

The window follows the selection: the arrow keys go on moving it in the list, and
the preview shows what is selected now. Space or Escape close it.
"""

from PySide6.QtCore import QMimeDatabase, QRect, QSize, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from filehouse.integration import style
from filehouse.qt_models import dates, previews
from filehouse.qt_models.icons import icon_for_content_type

# How much of the main window's size the preview opens with
_SHARE = 0.8
# A small picture is not blown up past this
_MAX_ENLARGEMENT = 2.0
_ICON = 128
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


class _Canvas(QWidget):
    """The picture, as large as fits and never distorted, or an icon with a line of text."""

    def __init__(self):
        super().__init__()
        self._image = None
        self._scaled = None
        self._icon = None
        self._text = ""
        self.setMinimumSize(style.px(240), style.px(180))

    def show_image(self, image):
        self._image = image
        self._scaled = None
        self.update()

    def show_icon(self, content_type, text):
        self._image = None
        self._icon = icon_for_content_type(content_type)
        self._text = text
        self.update()

    def clear(self):
        self._image = None
        self._icon = None
        self._text = ""
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._scaled = None

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self._image is not None:
            self._paint_image(painter)
        elif self._icon is not None:
            self._paint_icon(painter)

    def _paint_image(self, painter):
        ratio = self.devicePixelRatioF()
        if self._scaled is None:
            box = self.size() * ratio
            image = self._image
            # Down to what fits; up only a little, and only for something small
            if image.width() > box.width() or image.height() > box.height():
                scaled = image.scaled(
                    box,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            else:
                limit = QSize(
                    round(image.width() * _MAX_ENLARGEMENT),
                    round(image.height() * _MAX_ENLARGEMENT),
                )
                scaled = image.scaled(
                    box.boundedTo(limit),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            scaled.setDevicePixelRatio(ratio)
            self._scaled = scaled
        size = self._scaled.deviceIndependentSize()
        painter.drawImage(
            round((self.width() - size.width()) / 2),
            round((self.height() - size.height()) / 2),
            self._scaled,
        )

    def _paint_icon(self, painter):
        side = style.px(_ICON)
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

    def __init__(self, parent):
        super().__init__(parent, Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setWindowTitle("Preview")
        self._loader = previews.PreviewLoader(self)
        self._loader.ready.connect(self._on_loaded)
        self._token = 0
        self._shown = None  # what was last asked for, so the same again is not loaded twice

        self._canvas = _Canvas()
        self._details = QLabel()
        self._details.setObjectName("secondary")
        self._details.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._details.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        layout = QVBoxLayout(self)
        margin = style.px(12)
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.setSpacing(style.px(8))
        layout.addWidget(self._canvas, 1)
        layout.addWidget(self._details)

    def open_over(self, window):
        """Show it in the middle of `window`, at its size the first time."""
        if not self.isVisible():
            self.resize(round(window.width() * _SHARE), round(window.height() * _SHARE))
        self.show()
        self.raise_()
        self.activateWindow()

    # What it shows

    def show_item(self, info, path):
        """The file with this FileInfo, at `path` (None when it isn't on this computer)."""
        key = (path, info.modified, info.size)
        if key == self._shown:
            return
        self._shown = key
        self._token += 1
        self.setWindowTitle(info.display_name)
        kind = QMimeDatabase().mimeTypeForName(info.content_type).comment()
        self._details.setText(self._describe(info, kind))
        if info.is_dir:
            self._canvas.show_icon(info.content_type, "Folder")
        elif path is not None and previews.can_preview(info.content_type, info.size):
            # The type's icon until the picture is in
            self._canvas.show_icon(info.content_type, "Loading…")
            self._loader.request((self._token, info.name), path, info.content_type)
        else:
            why = "Not on this computer" if path is None else "No preview for this type"
            self._canvas.show_icon(info.content_type, why)

    def show_message(self, text):
        """For something that is not a single file."""
        self._shown = None
        self._token += 1
        self.setWindowTitle("Preview")
        self._details.setText("")
        self._canvas.clear()
        self._canvas.show_icon("application/octet-stream", text)

    def _describe(self, info, kind):
        size = self.locale().formattedDataSize(info.size) if not info.is_dir else ""
        modified = dates.friendly(info.modified, self.locale())
        return " · ".join(part for part in (kind, size, modified) if part)

    def _on_loaded(self, token, image):
        if token[0] != self._token:
            return
        if image.isNull():
            self._canvas.show_icon("application/octet-stream", "Could not read it")
            return
        self._canvas.show_image(image)
        self._details.setText(f"{self._details.text()} · {image.width()} × {image.height()}")

    # Keys

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key.Key_Space, Qt.Key.Key_Escape):
            self.hide()
        elif key in NAVIGATION_KEYS:
            self.navigate.emit(event)
        else:
            super().keyPressEvent(event)
