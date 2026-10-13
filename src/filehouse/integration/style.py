"""Maps the shell's theme to Qt: palette, font, sizes, icons and a stylesheet.

Colours and fonts go through `QPalette` and `QFont`. The stylesheet is for
shapes: rounded groups, cards, rows and menus.
"""

import math
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
from PySide6.QtWidgets import QApplication, QStyle

from filehouse import APP_ID
from filehouse.integration import theme as theme_reader

BASE_FONT_PX = 13
# The space a tab leaves to its right and below, unscaled, and the close button's
# distance from the tab's right edge
TAB_GAP = 4
TAB_CLOSE_INSET = 8

# How much of a tile its glyph fills, and how strong the tile's own colour is
_GLYPH_SHARE = 0.56
_TILE_ALPHA = 0.20

# The kinds of widget a platform theme may give a font of its own
_CLASS_FONTS = (
    "QAbstractItemView",
    "QHeaderView",
    "QListView",
    "QTreeView",
    "QToolButton",
    "QPushButton",
    "QCheckBox",
    "QRadioButton",
    "QComboBox",
    "QStatusBar",
    "QLabel",
    "QLineEdit",
    "QMenu",
    "QMenuBar",
    "QMessageBox",
    "QTipLabel",
    "QDockWidget",
    "QGroupBox",
)

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


# How much room the lists and the sidebar take. Chosen at the start (it is set before
# apply() and the widgets read it as they are made, like the scale), not a setting of the
# theme: the shell has none. Unscaled pixels, like every size here
DENSITIES = ("comfortable", "compact")
_DENSITY_SIZES = {
    "comfortable": {
        "row_padding": 6,
        "list_icon": 28,
        "grid_icon": 64,
        "grid_width": 116,
        "grid_height": 112,
        "sidebar_row": 42,
        "sidebar_heading": 36,
        "sidebar_icon": 28,
        # Height of a button in the toolbar's groups: icon buttons and breadcrumbs alike
        "control_height": 36,
        # Space between a toolbar group's edge and its buttons
        "group_padding": 4,
        # Height of a button that stands alone, like Open in the details card
        "button_height": 40,
        "tab_padding": 6,
        "menu_padding": 8,
        "card_margin": 16,
        "card_spacing": 12,
        "row_spacing": 10,
        "permissions_spacing": 14,
        "job_padding": 6,
    },
    "compact": {
        "row_padding": 3,
        "list_icon": 20,
        "grid_icon": 48,
        "grid_width": 104,
        "grid_height": 100,
        "sidebar_row": 32,
        "sidebar_heading": 28,
        "sidebar_icon": 20,
        "control_height": 30,
        "group_padding": 3,
        "button_height": 32,
        "tab_padding": 4,
        "menu_padding": 5,
        "card_margin": 12,
        "card_spacing": 8,
        "row_spacing": 6,
        "permissions_spacing": 9,
        "job_padding": 3,
    },
}
_density = "comfortable"


def set_density(name):
    """Use a density from now on: before apply() and before any widget is made."""
    global _density
    _density = name if name in _DENSITY_SIZES else "comfortable"


def dense(key):
    """A size that depends on the density, unscaled; `px()` it where it is used."""
    return _DENSITY_SIZES[_density][key]


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
    font = _font()
    app.setFont(font)
    app.setStyleSheet(_stylesheet())
    # The desktop's platform theme gives some kinds of widget a font of its own (Plasma's
    # system font, in points), which would win over the app's font for exactly the lists,
    # the toolbar and the status bar: they are told to use the shell's too. After the
    # style sheet, which is a new style, and a new style makes Qt read the platform's again
    for name in _CLASS_FONTS:
        app.setFont(font, name)
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
# A one-colour icon is for a button or a menu entry: the largest it is shown, unscaled
_GLYPH_LARGEST = 24


def _sizes(largest):
    """The sizes an icon needs that is shown no larger than `largest` pixels. Each one
    is a search of the icon theme and a drawing of its own, and a window has some
    seventy icons: the sizes nothing asks for were most of the time a start took."""
    needed = []
    for side in _ICON_SIZES:
        needed.append(side)
        # Qt makes a larger one smaller, never a smaller one larger
        if side >= largest:
            break
    return needed


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


def _build(draw, names, colour, sizes):
    source = _theme_icon(names)
    made = QIcon()
    for mode, shade in ((QIcon.Mode.Normal, colour), (QIcon.Mode.Disabled, color("textTertiary"))):
        for side in sizes:
            pixmap = draw(source, side, shade)
            if not pixmap.isNull():
                made.addPixmap(pixmap, mode)
    return made


def app_icon():
    """The app's own icon, for its windows and the tray: the lighthouse `install.sh`
    puts in the user's icons under the app's name, or the icon theme's file manager
    where that was not run."""
    return QIcon.fromTheme(APP_ID if QIcon.hasThemeIcon(APP_ID) else "system-file-manager")


def icon(*names, colour="textPrimary", menu=False):
    """A one-colour icon: the first of `names` the icon theme has, in a theme colour.
    `colour` is a name from the theme's colours. For buttons and menus: it is not
    made in the sizes a list or a preview would ask for, and with `menu` only in the
    one size a menu shows it at."""
    if menu:
        sizes = (QApplication.style().pixelMetric(QStyle.PixelMetric.PM_SmallIconSize),)
    else:
        sizes = _sizes(px(_GLYPH_LARGEST))
    return _build(_glyph, names, color(colour), sizes)


def tile(*names, colour, side=None):
    """An icon on a rounded tile, both in one of the palette colours ("green", "blue", …).
    `side` is the one size it is shown at, unscaled, for a tile with only one: without
    it the tile is made in every size."""
    if side is None:
        sizes = _ICON_SIZES
    else:
        # A tile is made of the screen's own pixels, and a dense screen has more of them
        # to the size: Qt asks for the larger one there, and does not make one larger
        sizes = sorted({px(side), math.ceil(px(side) * _pixel_ratio())})
    return _build(_tile, names, palette_color(colour), sizes)


def _pixel_ratio():
    # Of the densest screen there is
    return QApplication.instance().devicePixelRatio()


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
    control, group = dense("control_height"), dense("group_padding")
    tab_padding, menu_padding = px(dense("tab_padding")), px(dense("menu_padding"))
    return f"""
        QToolBar {{
            border: none; spacing: {px(8)}px; padding: {px(8)}px;
            background: {window};
        }}

        /* The rounded groups in the toolbar, the sidebar and the details card */
        #group, #card {{
            background: {card}; border: 1px solid {card_border}; border-radius: {large}px;
        }}
        #group {{ border-radius: {radius_within("large", control + 2 * group)}px; }}
        #group QToolButton {{
            border-radius: {radius_within("medium", control)}px; padding: {px(7)}px;
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
        QTreeView::item {{ padding: {px(dense("row_padding"))}px {px(6)}px; border: none; }}
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
            padding: {tab_padding}px {px(12)}px; margin: 0 {px(TAB_GAP)}px {px(TAB_GAP)}px 0;
            border: 1px solid transparent; background: transparent;
            border-radius: {radius_within("medium", control)}px;
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
            border: none; border-radius: {radius_within("large", dense("button_height"))}px;
            padding: 0 {px(10)}px; background: {raised};
        }}
        #card QPushButton:hover {{ background: {hover}; }}
        #card QPushButton:pressed {{ background: {pressed}; }}

        QMenu {{
            background: {card}; border: 1px solid {divider};
            border-radius: {large}px; padding: {px(6)}px;
        }}
        QMenu::item {{
            padding: {menu_padding}px {px(14)}px; border-radius: {medium}px; margin: 0 {px(2)}px;
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
