"""The look: tinted icons, icon tiles and which kind of file gets which colour."""

import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QIcon

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
            style.radius_within("large", style.BUTTON_HEIGHT) <= style.px(style.BUTTON_HEIGHT) / 2
        )
        pane = DetailsPane()
        qtbot.addWidget(pane)
        pane.open_button.show()
        pane.resize(260, 400)
        pane.show()
        # Of the card, not the button alone: a widget grabbed by itself gets a filled background
        image = pane.grab(pane.open_button.geometry()).toImage()
        raised = style.color("surfaceRaised")
        # Beside the text, not under it
        assert same_colour(image.pixelColor(image.width() // 8, image.height() // 2), raised)
        assert not same_colour(image.pixelColor(0, 0), raised)
        # The top corners: in the roundest theme the card's own border passes the lower ones
        assert not same_colour(image.pixelColor(image.width() - 1, 0), raised)
    finally:
        style.apply(qapp, theme.merge(theme.DEFAULTS, {}))
        qapp.setStyleSheet("")
