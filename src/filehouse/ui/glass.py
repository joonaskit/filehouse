"""The light on a card of glass, as the shell's `GlassSurface` draws it: a rim that is
bright along the top and the bottom, where thick glass bends the most light, and a
softer band inside it. The sheen is the card's background (`style`); this is drawn over
the card's content, at its edge only, where a card has its padding."""

from PySide6.QtCore import QEvent, QRectF, Qt
from PySide6.QtGui import QLinearGradient, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

from filehouse.integration import style

# Of the rim, and of the band inside it, unscaled
_RIM = 1
_BAND = 3


class GlassLight(QWidget):
    """Lights `card` while the app is glass. `radius()` is the card's corner radius."""

    def __init__(self, card, radius):
        super().__init__(card)
        self._radius = radius
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        card.installEventFilter(self)
        style.themed.changed.connect(self._follow)
        self._follow()

    def eventFilter(self, watched, event):
        # Over the card, and over what is put in it later (a list's viewport)
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.ChildAdded):
            self.setGeometry(watched.rect())
            self.raise_()
        return False

    def _follow(self):
        self.setVisible(style.glass())
        self.update()

    def paintEvent(self, _event):
        light = style.glass_light()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        height = self.height()
        # Inside the card's own border, which on glass is the hairline
        border = 1
        rim = border + style.px(_RIM)
        band = rim + style.px(_BAND)
        painter.setBrush(
            self._gradient(
                height,
                (0.0, light["rimTop"]),
                (0.5, light["rimSide"]),
                (1.0, light["rimBottom"]),
            )
        )
        painter.drawPath(self._ring(border, rim))
        painter.setBrush(
            self._gradient(
                height,
                (0.0, light["bandTop"]),
                (0.5, Qt.GlobalColor.transparent),
                (1.0, light["bandBottom"]),
            )
        )
        painter.drawPath(self._ring(rim, band))

    def _ring(self, outer, inner):
        """What lies between two rounded rectangles `outer` and `inner` pixels in."""
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.OddEvenFill)
        for inset in (outer, inner):
            corner = max(0, self._radius() - inset)
            area = QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)
            path.addRoundedRect(area, corner, corner)
        return path

    @staticmethod
    def _gradient(height, *stops):
        gradient = QLinearGradient(0, 0, 0, height)
        for position, colour in stops:
            gradient.setColorAt(position, colour)
        return gradient


def light(card, radius=None):
    """Light `card` while glass; its corners are `radius()`, the large radius without."""
    return GlassLight(card, radius or (lambda: style.radius("large")))
