"""Maps the shell's theme to Qt: palette, font, sizes, icons and a stylesheet.

Colours and fonts go through `QPalette` and `QFont`. The stylesheet is for
shapes: rounded groups, cards, rows and menus.
"""

from functools import cache

from PySide6.QtCore import QObject, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QPainter,
    QPalette,
    QPixmap,
)

from filehouse.integration import theme as theme_reader

BASE_FONT_PX = 13
# Height of a button in the toolbar's groups, unscaled: icon buttons and breadcrumbs alike
CONTROL_HEIGHT = 36
# Space between a toolbar group's edge and its buttons, unscaled
GROUP_PADDING = 4
# Height of a button that stands alone, like Open in the details card, unscaled
BUTTON_HEIGHT = 40
# The space a tab leaves to its right and below, unscaled, and the close button's
# distance from the tab's right edge
TAB_GAP = 4
TAB_CLOSE_INSET = 8

# How much of a tile its glyph fills, and how strong the tile's own colour is
_GLYPH_SHARE = 0.56
_TILE_ALPHA = 0.20

# The theme in use. Complete defaults until apply() runs, so sizes work in tests.
_theme = theme_reader.merge(theme_reader.DEFAULTS, {})
# Goes up each time a theme is applied
generation = 0


class _Themed(QObject):
    # A theme was applied while the app runs: whatever holds an icon or a colour
    # made from the old one has to make it again
    changed = Signal()


themed = _Themed()


def px(size):
    """An unscaled pixel size at the user's UI scale."""
    return theme_reader.scaled(_theme, size)


def font_px(size):
    """An unscaled font size in pixels at the user's font and UI scale."""
    return theme_reader.font_scaled(_theme, size)


def color(name):
    return QColor(_theme["colors"][name])


# The window's layers, as the shell's own windows are built (its clipboard history, for
# one): the window is the theme's `surface`, not its `background`, which is pitch black
# in the dark scheme and harsh behind a whole window. What lies on the window is that
# colour with a little of the text colour mixed in, so the light scheme follows by itself.
_LAYERS = {"window": 0.0, "card": 0.04, "raised": 0.10}
# The hairline around a card: it is what sets a card apart from the window
_CARD_BORDER = 0.06


def layer(name):
    """The colour of one of the window's layers: "window", the "card"s on it (sidebar,
    details, toolbar groups, menus) or the "raised" controls on a card."""
    base, text, share = color("surface"), color("textPrimary"), _LAYERS[name]
    return QColor(
        round(base.red() + (text.red() - base.red()) * share),
        round(base.green() + (text.green() - base.green()) * share),
        round(base.blue() + (text.blue() - base.blue()) * share),
    )


def palette_color(name):
    """One of the theme's eight decoration colours: "green", "blue", "orange", …"""
    return QColor(_theme["colors"]["palette"][name])


def radius(name):
    """`small`, `medium` or `large`, in pixels. Follows the shell's corner roundness."""
    return px(_theme["radius"][name])


def radius_within(name, height):
    """A radius for something `height` unscaled pixels tall: never more than half of it.
    A stylesheet draws square corners when the radius doesn't fit, so with the shell
    set to "Round" a low button would lose its corners altogether."""
    return min(radius(name), px(height) // 2)


def overlay(alpha):
    """The text colour at `alpha`: for hover, dividers and the like.
    From the text colour, not white, so it works on a light background too."""
    return _rgba(color("textPrimary"), alpha)


def tint(name, alpha):
    """A theme colour at `alpha`, for a stylesheet."""
    return _rgba(color(name), alpha)


def _rgba(colour, alpha):
    return f"rgba({colour.red()}, {colour.green()}, {colour.blue()}, {alpha})"


def apply(app, theme, live=False):
    """Style the whole application. Call before creating any window.

    With `live`, the app is already running with an earlier theme: colours and corner
    radii change at once, but the scales and the fonts stay as they are, because sizes
    and fonts are fixed in the widgets when they are made. Returns whether anything
    changed; `themed.changed` is emitted when it did."""
    global _theme, generation
    if live:
        theme = {**theme, "scale": _theme["scale"], "fonts": _theme["fonts"]}
        if theme == _theme:
            return False
    _theme = theme
    generation += 1
    app.setPalette(_palette())
    app.setFont(_font())
    app.setStyleSheet(_stylesheet())
    app.styleHints().setShowShortcutsInContextMenus(True)
    if live:
        themed.changed.emit()
    return True


# Icons
#
# Drawn once, as ordinary pixmaps in the sizes the app uses. (A custom icon engine
# would follow the theme by itself, but Qt can outlive Python at exit and then
# calls into an engine that is gone.) Icons are made after apply(), and
# `generation` tells caches when the theme they were made for is no longer in use.

_ICON_SIZES = (16, 20, 24, 28, 32, 48, 64, 128)


def _theme_icon(names):
    for name in names:
        if QIcon.hasThemeIcon(name):
            return QIcon.fromTheme(name)
    return QIcon()


def _tinted(source, side, colour):
    """`source` at `side` pixels with every pixel in `colour`, keeping its shape."""
    pixmap = source.pixmap(QSize(side, side))
    if pixmap.isNull():
        return pixmap
    painter = QPainter(pixmap)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(pixmap.rect(), colour)
    painter.end()
    return pixmap


def _glyph(source, side, colour):
    return _tinted(source, side, colour)


def _tile(source, side, colour):
    pixmap = QPixmap(side, side)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    back = QColor(colour)
    back.setAlphaF(_TILE_ALPHA)
    painter.setBrush(back)
    corner = side * 0.28
    painter.drawRoundedRect(QRectF(0, 0, side, side), corner, corner)
    inner = round(side * _GLYPH_SHARE)
    glyph = _tinted(source, inner, colour)
    if not glyph.isNull():
        painter.drawPixmap((side - inner) // 2, (side - inner) // 2, glyph)
    painter.end()
    return pixmap


def _build(draw, names, colour):
    source = _theme_icon(names)
    made = QIcon()
    for mode, shade in ((QIcon.Mode.Normal, colour), (QIcon.Mode.Disabled, color("textTertiary"))):
        for side in _ICON_SIZES:
            pixmap = draw(source, side, shade)
            if not pixmap.isNull():
                made.addPixmap(pixmap, mode)
    return made


def icon(*names, colour="textPrimary"):
    """A one-colour icon: the first of `names` the icon theme has, in a theme colour.
    `colour` is a name from the theme's colours."""
    return _build(_glyph, names, color(colour))


def tile(*names, colour):
    """An icon on a rounded tile, both in one of the palette colours ("green", "blue", …)."""
    return _build(_tile, names, palette_color(colour))


# Palette, font, stylesheet


def _palette():
    palette = QPalette()
    roles = {
        QPalette.ColorRole.WindowText: "textPrimary",
        QPalette.ColorRole.Text: "textPrimary",
        QPalette.ColorRole.ButtonText: "textPrimary",
        QPalette.ColorRole.ToolTipText: "textPrimary",
        QPalette.ColorRole.PlaceholderText: "textSecondary",
        QPalette.ColorRole.Highlight: "accent",
        QPalette.ColorRole.HighlightedText: "onAccent",
        QPalette.ColorRole.Link: "accent",
    }
    for role, name in roles.items():
        palette.setColor(role, color(name))
    layers = {
        QPalette.ColorRole.Window: "window",
        QPalette.ColorRole.Base: "window",
        QPalette.ColorRole.AlternateBase: "card",
        QPalette.ColorRole.Button: "raised",
        QPalette.ColorRole.ToolTipBase: "raised",
    }
    for role, name in layers.items():
        palette.setColor(role, layer(name))
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, color("textTertiary"))
    return palette


def _font():
    installed = set(QFontDatabase.families())
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    for family in _theme["fonts"]["families"]:
        if family in installed:
            font = QFont(family)
            break
    font.setPixelSize(theme_reader.font_scaled(_theme, BASE_FONT_PX))
    return font


def fixed_font():
    """The system's fixed-width font at the theme's size: for permissions and the like."""
    return _fixed_font(generation)


@cache
def _fixed_font(_theme_generation):
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPixelSize(theme_reader.font_scaled(_theme, BASE_FONT_PX - 1))
    return font


def _stylesheet():
    colors = _theme["colors"]
    small, medium, large = radius("small"), radius("medium"), radius("large")
    hover, pressed, divider = overlay(0.08), overlay(0.14), overlay(0.10)
    selected = tint("accent", 0.22)
    window, card, raised = (layer(name).name() for name in ("window", "card", "raised"))
    card_border = overlay(_CARD_BORDER)
    small_font = theme_reader.font_scaled(_theme, 11)
    return f"""
        QToolBar {{
            border: none; spacing: {px(8)}px; padding: {px(8)}px;
            background: {window};
        }}

        /* The favourites under it: flat buttons on the window, as a bookmarks bar */
        QToolBar#favourites {{ padding: 0 {px(8)}px {px(4)}px {px(8)}px; spacing: {px(2)}px; }}
        QToolBar#favourites QToolButton {{ padding: {px(4)}px {px(10)}px {px(4)}px {px(6)}px; }}

        /* The rounded groups in the toolbar, the sidebar and the details card */
        #group, #card {{
            background: {card}; border: 1px solid {card_border}; border-radius: {large}px;
        }}
        #group {{ border-radius: {radius_within("large", CONTROL_HEIGHT + 2 * GROUP_PADDING)}px; }}
        #group QToolButton {{
            border-radius: {radius_within("medium", CONTROL_HEIGHT)}px; padding: {px(7)}px;
        }}

        QToolButton {{
            border: none; border-radius: {small}px; padding: {px(5)}px;
            background: transparent;
        }}
        QToolButton:hover {{ background: {hover}; }}
        QToolButton:pressed {{ background: {pressed}; }}
        QToolButton:checked {{ background: {raised}; }}

        QLineEdit {{
            border: 1px solid {divider}; border-radius: {medium}px;
            padding: {px(4)}px {px(8)}px; background: {raised};
        }}
        QLineEdit:focus {{ border-color: {colors["accent"]}; }}

        QTreeView, QListView {{ border: none; outline: none; background: transparent; }}
        QTreeView::item {{ padding: {px(6)}px {px(6)}px; border: none; }}
        QTreeView::item:hover, QListView::item:hover {{ background: {hover}; }}
        QTreeView::item:selected, QListView::item:selected {{
            background: {selected}; color: {colors["textPrimary"]};
        }}
        QTreeView::item:first, QTreeView::item:only-one {{
            border-top-left-radius: {medium}px; border-bottom-left-radius: {medium}px;
        }}
        QTreeView::item:last, QTreeView::item:only-one {{
            border-top-right-radius: {medium}px; border-bottom-right-radius: {medium}px;
        }}
        QListView::item {{ border-radius: {medium}px; padding: {px(4)}px; }}

        /* A list beside another one: the active one has the accent colour */
        #pane[split="true"] {{
            border: 1px solid {card_border}; border-radius: {large}px; padding: {px(2)}px;
        }}
        #pane[split="true"][active="true"] {{ border-color: {colors["accent"]}; }}

        QTabBar {{ background: transparent; }}
        QTabBar::tab {{
            padding: {px(6)}px {px(12)}px; margin: 0 {px(TAB_GAP)}px {px(TAB_GAP)}px 0;
            border: 1px solid transparent; background: transparent;
            border-radius: {radius_within("medium", CONTROL_HEIGHT)}px;
            color: {colors["textSecondary"]};
        }}
        QTabBar::tab:hover {{ background: {hover}; }}
        QTabBar::tab:selected {{
            background: {card}; border-color: {card_border}; color: {colors["textPrimary"]};
        }}

        #sidebar {{
            padding: {px(8)}px; background: {card}; border: 1px solid {card_border};
            border-radius: {large}px;
        }}
        QListWidget#sidebar::item {{ padding: 0 {px(6)}px; border-radius: {medium}px; }}
        QListWidget#sidebar::item:disabled {{ color: {colors["textSecondary"]}; }}

        QHeaderView {{ background: transparent; }}
        QHeaderView::section {{
            border: none; padding: {px(6)}px {px(6)}px; background: transparent;
            color: {colors["textSecondary"]}; font-size: {small_font}px;
        }}
        QSplitter::handle {{ background: transparent; }}

        /* The details card */
        #secondary {{ color: {colors["textSecondary"]}; font-size: {small_font}px; }}
        #divider {{ background: {divider}; border: none; }}
        #card QPushButton {{
            border: none; border-radius: {radius_within("large", BUTTON_HEIGHT)}px;
            padding: 0 {px(10)}px; background: {raised};
        }}
        #card QPushButton:hover {{ background: {hover}; }}
        #card QPushButton:pressed {{ background: {pressed}; }}

        QMenu {{
            background: {card}; border: 1px solid {divider};
            border-radius: {large}px; padding: {px(6)}px;
        }}
        QMenu::item {{
            padding: {px(8)}px {px(14)}px; border-radius: {medium}px; margin: 0 {px(2)}px;
        }}
        QMenu::item:selected {{ background: {hover}; color: {colors["textPrimary"]}; }}
        /* What is switched on: the menu draws no tick of its own with this style */
        QMenu::item:checked {{ background: {selected}; color: {colors["textPrimary"]}; }}
        QMenu::item:checked:selected {{ background: {tint("accent", 0.34)}; }}
        QMenu::item:disabled {{ color: {colors["textTertiary"]}; }}
        QMenu::icon {{ padding-left: {px(10)}px; }}
        QMenu::separator {{ height: 1px; background: {divider}; margin: {px(6)}px {px(8)}px; }}

        QScrollBar:vertical {{ background: transparent; width: {px(10)}px; margin: {px(2)}px; }}
        QScrollBar:horizontal {{ background: transparent; height: {px(10)}px; margin: {px(2)}px; }}
        QScrollBar::handle {{ background: {pressed}; border-radius: {px(3)}px; }}
        QScrollBar::handle:vertical {{ min-height: {px(28)}px; }}
        QScrollBar::handle:horizontal {{ min-width: {px(28)}px; }}
        QScrollBar::handle:hover {{ background: {overlay(0.3)}; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

        QStatusBar, QStatusBar QLabel {{ color: {colors["textSecondary"]}; }}
        QStatusBar QLabel {{ padding: 0 {px(8)}px; }}
        QStatusBar::item {{ border: none; }}
        #jobStatus {{ border-radius: {radius_within("small", 24)}px; padding: 0; }}
        #operations {{ border-top: 1px solid {divider}; }}
        #operationsHeader {{ border-radius: 0; padding: {px(6)}px {px(10)}px; }}
        QProgressBar {{
            border: none; border-radius: {px(3)}px; background: {raised};
        }}
        QProgressBar::chunk {{ border-radius: {px(3)}px; background: {colors["accent"]}; }}
    """
