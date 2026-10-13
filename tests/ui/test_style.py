"""The look: tinted icons, icon tiles and which kind of file gets which colour."""

import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import QStyle

from filehouse.integration import style, theme
from filehouse.qt_models import icons


@pytest.mark.parametrize(
    "content_type, kind",
    [
        ("inode/directory", "folder"),
        ("image/png", "image"),
        ("video/mp4", "video"),
        ("audio/flac", "audio"),
        ("application/zip", "archive"),
        ("application/x-compressed-tar", "archive"),
        ("application/pdf", "document"),
        ("text/markdown", "document"),
        ("application/vnd.oasis.opendocument.spreadsheet", "document"),
        ("text/x-python", "code"),
        ("text/x-qml", "code"),
        ("application/x-shellscript", "code"),
        ("application/json", "code"),
        ("application/octet-stream", "other"),
    ],
)
def test_kinds_of_files(content_type, kind):
    assert icons.kind_of(content_type) == kind


def pixel(icon, x, y, size=40, mode=QIcon.Mode.Normal):
    return icon.pixmap(QSize(size, size), mode).toImage().pixelColor(x, y)


def same_colour(a, b):
    """Equal apart from the rounding that drawing with transparency brings."""
    a, b = QColor(a), QColor(b)
    channels = zip((a.red(), a.green(), a.blue()), (b.red(), b.green(), b.blue()), strict=True)
    return all(abs(x - y) <= 4 for x, y in channels)


def test_a_tile_is_a_rounded_square_in_the_palette_colour(qtbot):
    tile = style.tile("no-such-icon-anywhere", colour="green")
    green = style.palette_color("green")

    middle = pixel(tile, 20, 6)
    corner = pixel(tile, 0, 0)

    # The tile is there, faint, even when the icon theme has no glyph for it
    assert same_colour(middle, green)
    assert 0 < middle.alpha() < 128
    assert corner.alpha() == 0


def test_icons_for_files_are_tiles_and_are_made_once_per_type(qtbot):
    folder = icons.icon_for_content_type("inode/directory")
    cyan = style.palette_color("cyan")

    assert icons.icon_for_content_type("inode/directory") is folder
    edge = pixel(folder, 20, 3)
    assert same_colour(edge, cyan)


def test_icons_made_after_a_theme_change_use_the_new_theme(qtbot, qapp):
    before = pixel(icons.icon_for_content_type("application/octet-stream"), 20, 6)
    custom = theme.merge(theme.DEFAULTS, {"colors": {"palette": {"blue": "#112233"}}})

    try:
        style.apply(qapp, custom)
        after = pixel(icons.icon_for_content_type("application/octet-stream"), 20, 6)
    finally:
        style.apply(qapp, theme.merge(theme.DEFAULTS, {}))

    assert same_colour(after, "#112233")
    assert not same_colour(before, after)


def test_overlays_come_from_the_text_colour_so_light_themes_work(qtbot, qapp):
    light = theme.merge(theme.DEFAULTS, {"scheme": "light", "colors": {"textPrimary": "#101010"}})
    try:
        style.apply(qapp, light)
        assert style.overlay(0.1) == "rgba(16, 16, 16, 0.1)"
    finally:
        style.apply(qapp, theme.merge(theme.DEFAULTS, {}))
    assert style.overlay(0.1) == "rgba(255, 255, 255, 0.1)"


def test_radius_follows_the_theme_and_the_scale(qtbot, qapp):
    square = theme.merge(
        theme.DEFAULTS, {"radius": {"small": 2, "medium": 4, "large": 6}, "scale": {"ui": 1.5}}
    )
    try:
        style.apply(qapp, square)
        assert (style.radius("small"), style.radius("large")) == (3, 9)
    finally:
        style.apply(qapp, theme.merge(theme.DEFAULTS, {}))


def test_a_disabled_icon_is_drawn_in_the_faint_text_colour(qtbot):
    tile = style.tile("no-such-icon-anywhere", colour="red")
    faint = QColor(theme.DEFAULTS["colors"]["textTertiary"])

    disabled = pixel(tile, 20, 6, mode=QIcon.Mode.Disabled)

    assert same_colour(disabled, faint)
    assert not same_colour(disabled, style.palette_color("red"))


def sides(icon):
    return sorted({size.width() for size in icon.availableSizes()})


def test_an_icon_for_a_button_is_made_in_the_small_sizes_only(qtbot, monkeypatch):
    # Whatever the icon theme of the machine has: a source that draws at any size
    source = QPixmap(256, 256)
    source.fill(QColor("white"))
    monkeypatch.setattr(style, "_theme_icon", lambda names: QIcon(source))

    made = style.icon("anything")

    assert sides(made) == [16, 20, 24]
    # Asked for a size between two, it is the larger one made smaller
    assert same_colour(pixel(made, 9, 9, size=18), style.color("textPrimary"))
    assert sides(style.tile("anything", colour="green")) == list(style._ICON_SIZES)


def test_the_sizes_of_a_button_icon_follow_the_scale(qtbot, qapp, monkeypatch):
    source = QPixmap(256, 256)
    source.fill(QColor("white"))
    monkeypatch.setattr(style, "_theme_icon", lambda names: QIcon(source))
    try:
        style.apply(qapp, theme.merge(theme.DEFAULTS, {"scale": {"ui": 1.5}}))
        # 24 is 36 at this scale: up to the first size that is not smaller
        assert sides(style.icon("anything")) == [16, 20, 24, 28, 32, 48]
    finally:
        style.apply(qapp, theme.merge(theme.DEFAULTS, {}))


def test_a_tile_shown_at_one_size_is_made_for_a_dense_screen_too(qtbot, monkeypatch):
    # Shown at 28 on a screen with twice the pixels, the tile of 28 would be stretched
    monkeypatch.setattr(style, "_pixel_ratio", lambda: 2.0)

    tile = style.tile("no-such-icon-anywhere", colour="green", side=28)

    assert sides(tile) == [28, 56]
    assert sides(style.tile("no-such-icon-anywhere", colour="green", side=20)) == [20, 40]
    monkeypatch.setattr(style, "_pixel_ratio", lambda: 1.5)
    assert sides(style.tile("no-such-icon-anywhere", colour="green", side=28)) == [28, 42]


def test_a_tile_shown_at_one_size_is_made_in_that_size(qtbot, qapp):
    try:
        style.apply(qapp, theme.merge(theme.DEFAULTS, {"scale": {"ui": 1.5}}))
        tile = style.tile("no-such-icon-anywhere", colour="green", side=28)
    finally:
        style.apply(qapp, theme.merge(theme.DEFAULTS, {}))

    assert sides(tile) == [42]
    assert same_colour(pixel(tile, 21, 6, size=42), style.palette_color("green"))
    disabled = pixel(tile, 21, 6, size=42, mode=QIcon.Mode.Disabled)
    assert not same_colour(disabled, style.palette_color("green"))


@pytest.mark.parametrize("large", [14, 21, 60])
def test_a_button_keeps_round_corners_however_round_the_theme_is(qtbot, qapp, large):
    # A stylesheet drops a radius that is more than half the height and draws square
    # corners: with the shell set to "Round" (21 at scale 1.1) the Open button was a box
    from filehouse.ui.details_pane import DetailsPane

    round_theme = theme.merge(
        theme.DEFAULTS,
        {"radius": {"small": 9, "medium": 15, "large": large}, "scale": {"ui": 1.1}},
    )
    try:
        style.apply(qapp, round_theme)
        assert (
            style.radius_within("large", style.dense("button_height"))
            <= style.px(style.dense("button_height")) / 2
        )
        pane = DetailsPane()
        qtbot.addWidget(pane)
        pane.open_button.show()
        pane.resize(260, 400)
        pane.show()
        # Of the card, not the button alone: a widget grabbed by itself gets a filled background
        image = pane.grab(pane.open_button.geometry()).toImage()
        raised = style.layer("raised")
        # Beside the text, not under it
        assert same_colour(image.pixelColor(image.width() // 8, image.height() // 2), raised)
        assert not same_colour(image.pixelColor(0, 0), raised)
        # The top corners: in the roundest theme the card's own border passes the lower ones
        assert not same_colour(image.pixelColor(image.width() - 1, 0), raised)
    finally:
        style.apply(qapp, theme.merge(theme.DEFAULTS, {}))
        qapp.setStyleSheet("")


def test_the_window_is_the_surface_colour_and_cards_are_a_shade_off_it(qtbot, qapp):
    from PySide6.QtGui import QPalette

    try:
        style.apply(qapp, theme.merge(theme.DEFAULTS, {}))
        palette = qapp.palette()
        # Not the theme's background, which is pitch black
        assert palette.color(QPalette.ColorRole.Window).name() == "#1c1c1e"
        assert palette.color(QPalette.ColorRole.Base).name() == "#1c1c1e"
        assert style.layer("card").name() == "#252527"
        assert style.layer("raised").name() == "#333334"

        # In a light theme the layers get darker, not lighter
        light = theme.merge(
            theme.DEFAULTS,
            {"scheme": "light", "colors": {"surface": "#f1ebe1", "textPrimary": "#101010"}},
        )
        style.apply(qapp, light)
        assert style.layer("window").name() == "#f1ebe1"
        assert style.layer("card").lightness() < style.layer("window").lightness()
        assert style.layer("raised").lightness() < style.layer("card").lightness()
    finally:
        style.apply(qapp, theme.merge(theme.DEFAULTS, {}))
        qapp.setStyleSheet("")


def test_every_kind_of_widget_gets_the_shells_font_even_under_plasmas_platform_theme():
    """Plasma's platform theme gives lists, tool buttons and the status bar a font of their
    own, in points. Run apart, with that theme where it is installed: where it is not, the
    fonts are the app's all the same."""
    import os
    import subprocess
    import sys

    code = """
import sys
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QStatusBar, QToolButton, QTreeView, QHeaderView
app = QApplication([])
from filehouse.integration import style, theme
loaded = theme.merge(theme.DEFAULTS, {"fonts": {"families": ["Noto Sans", "sans-serif"]}})
style.apply(app, loaded)
for widget in (QTreeView(), QHeaderView(Qt.Orientation.Horizontal), QToolButton(), QStatusBar()):
    widget.ensurePolished()
    print(type(widget).__name__, widget.font().pixelSize())
print("app", app.font().pixelSize())
"""
    environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QPA_PLATFORMTHEME": "kde"}
    result = subprocess.run(
        [sys.executable, "-c", code], env=environment, capture_output=True, text=True, timeout=60
    )

    sizes = dict(line.split() for line in result.stdout.split("\n") if line.strip())
    assert result.returncode == 0, result.stderr
    assert sizes and int(sizes["app"]) > 0
    # In pixels, and the app's: the platform's would be a size in points, and say -1
    assert all(int(size) == int(sizes["app"]) for size in sizes.values()), sizes


def test_density_sizes_follow_the_choice():
    try:
        style.set_density("compact")
        compact = style.dense("sidebar_row")
        style.set_density("nonsense")
        assert style.dense("sidebar_row") > compact
    finally:
        style.set_density("comfortable")


def test_density_is_remembered(home_dir):
    from filehouse.ui import settings

    assert settings.density() == "comfortable"
    settings.set_density("compact")
    assert settings.density() == "compact"
    settings.set_density("cramped")
    assert settings.density() == "comfortable"


def test_an_icon_for_a_menu_is_made_in_the_size_a_menu_shows(qtbot, qapp, monkeypatch):
    source = QPixmap(256, 256)
    source.fill(QColor("white"))
    monkeypatch.setattr(style, "_theme_icon", lambda names: QIcon(source))
    side = qapp.style().pixelMetric(QStyle.PixelMetric.PM_SmallIconSize)

    made = style.icon("anything", colour="danger", menu=True)

    assert sides(made) == [side]
    assert same_colour(pixel(made, 2, 2, size=side), style.color("danger"))


@pytest.mark.parametrize(
    "installed, asked_for",
    [({"filehouse", "system-file-manager"}, "filehouse"), (set(), "system-file-manager")],
)
def test_the_apps_icon_is_its_own_where_installed_and_the_file_managers_elsewhere(
    monkeypatch, installed, asked_for
):
    class IconTheme:
        hasThemeIcon = staticmethod(lambda name: name in installed)
        fromTheme = staticmethod(lambda name: name)

    monkeypatch.setattr(style, "QIcon", IconTheme)

    assert style.app_icon() == asked_for
