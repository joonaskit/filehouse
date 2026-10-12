"""The look follows the shell's theme file while the app runs."""

import json
import os

import pytest
from gi.repository import Gio
from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from ui_helpers import TIMEOUT

from filehouse.integration import style, theme
from filehouse.integration.theme_watcher import ThemeWatcher
from filehouse.ui.main_window import MainWindow

SETTLE_MS = 30


@pytest.fixture(autouse=True)
def restore_theme(qapp):
    """The theme is the process's: give it back as it was."""
    before = style._theme
    yield
    style.apply(qapp, before)


@pytest.fixture
def theme_file(tmp_path):
    return tmp_path / "config" / "quickshell-island" / "theme.json"


def write(path, colors=None, **sections):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"version": 1, "colors": colors or {}, **sections}
    # As the shell does: a new file moved over the old one
    temporary = path.with_name("theme.json.tmp")
    temporary.write_text(json.dumps(data))
    os.replace(temporary, path)


def accent():
    return style.color("accent").name()


def changed(qtbot, expected):
    qtbot.waitUntil(lambda: accent() == expected, timeout=TIMEOUT)


def test_a_live_theme_keeps_the_scales_and_fonts(qapp):
    style.apply(qapp, theme.merge(theme.DEFAULTS, {"scale": {"ui": 1.0, "font": 1.0}}))
    new = theme.merge(
        theme.DEFAULTS,
        {
            "colors": {"accent": "#ff0000"},
            "scale": {"ui": 1.5, "font": 1.2},
            "radius": {"large": 30},
        },
    )

    assert style.apply(qapp, new, live=True)

    assert accent() == "#ff0000"
    assert style.radius("large") == 30
    # Sizes are fixed in the widgets that exist: they wait for the next start
    assert style.px(10) == 10
    assert style._theme["scale"] == {"ui": 1.0, "font": 1.0}


def test_applying_the_same_theme_live_does_nothing(qapp, qtbot):
    style.apply(qapp, theme.merge(theme.DEFAULTS, {}))
    generation = style.generation

    with qtbot.assertNotEmitted(style.themed.changed):
        assert not style.apply(qapp, theme.merge(theme.DEFAULTS, {}), live=True)
        # Only the scale differs, and that waits
        assert not style.apply(qapp, theme.merge(theme.DEFAULTS, {"scale": {"ui": 2.0}}), live=True)

    assert style.generation == generation


def test_the_file_is_followed_as_the_shell_replaces_it(qapp, qtbot, theme_file):
    write(theme_file, {"accent": "#112233"})
    style.apply(qapp, theme.load_theme(theme_file))
    watcher = ThemeWatcher(qapp, theme_file, SETTLE_MS)

    write(theme_file, {"accent": "#445566"})
    changed(qtbot, "#445566")
    # And again: a file that was replaced is watched in its new form
    write(theme_file, {"accent": "#778899"})
    changed(qtbot, "#778899")
    watcher.deleteLater()


def test_a_file_changed_in_place_is_followed(qapp, qtbot, theme_file):
    write(theme_file, {"accent": "#112233"})
    style.apply(qapp, theme.load_theme(theme_file))
    watcher = ThemeWatcher(qapp, theme_file, SETTLE_MS)

    theme_file.write_text(json.dumps({"version": 1, "colors": {"accent": "#abcdef"}}))

    changed(qtbot, "#abcdef")
    watcher.deleteLater()


def test_a_theme_that_appears_later_is_followed(qapp, qtbot, theme_file):
    # The shell has never run: not even its directory is there
    theme_file.parent.parent.mkdir(parents=True)
    style.apply(qapp, theme.load_theme(theme_file))
    assert accent() == theme.DEFAULTS["colors"]["accent"]
    watcher = ThemeWatcher(qapp, theme_file, SETTLE_MS)

    write(theme_file, {"accent": "#123456"})

    changed(qtbot, "#123456")
    watcher.deleteLater()


def test_a_file_that_goes_or_breaks_leaves_the_look(qapp, qtbot, theme_file):
    write(theme_file, {"accent": "#112233"})
    style.apply(qapp, theme.load_theme(theme_file))
    watcher = ThemeWatcher(qapp, theme_file, SETTLE_MS)

    theme_file.write_text("{ not json")
    qtbot.wait(SETTLE_MS * 6)
    assert accent() == "#112233"

    theme_file.unlink()
    qtbot.wait(SETTLE_MS * 6)
    assert accent() == "#112233"

    write(theme_file, {"accent": "#334455"})
    changed(qtbot, "#334455")
    watcher.deleteLater()


def pixel_colour(icon):
    """The colour of the opaque pixels of an icon (it has one)."""
    image = icon.pixmap(QSize(24, 24)).toImage()
    for x in range(image.width()):
        for y in range(image.height()):
            colour = image.pixelColor(x, y)
            if colour.alpha() > 200:
                return colour.name()
    return None


@pytest.fixture
def icon_theme():
    """The offscreen platform has no icon theme: use one that is installed, or skip."""
    paths, name = QIcon.themeSearchPaths(), QIcon.themeName()
    QIcon.setThemeSearchPaths(["/usr/share/icons", *paths])
    QIcon.setThemeName("breeze")
    if not QIcon.hasThemeIcon("go-previous-symbolic"):
        QIcon.setThemeSearchPaths(paths)
        QIcon.setThemeName(name)
        pytest.skip("no icon theme with symbolic icons is installed")
    yield
    QIcon.setThemeSearchPaths(paths)
    QIcon.setThemeName(name)


def test_the_icons_of_an_open_window_are_drawn_again(qapp, qtbot, tmp_path, icon_theme):
    style.apply(qapp, theme.merge(theme.DEFAULTS, {"colors": {"textPrimary": "#ffffff"}}))
    (tmp_path / "folder").mkdir()
    window = MainWindow(Gio.File.new_for_path(str(tmp_path)))
    qtbot.addWidget(window)
    window.show()
    window.new_tab()
    qtbot.waitUntil(lambda: not window._model.loading, timeout=TIMEOUT)
    close = window._tab_bar.tabButton(0, window._tab_bar.ButtonPosition.RightSide)
    place = window._sidebar.item(1)
    place_name = place.text()
    before = pixel_colour(window._back.icon()), pixel_colour(close.icon())
    tile = place.icon().pixmap(QSize(28, 28)).toImage().pixelColor(14, 14).name()
    assert before == ("#ffffff", "#ffffff")

    style.apply(
        qapp,
        theme.merge(
            theme.DEFAULTS,
            {
                "colors": {
                    "textPrimary": "#202020",
                    "palette": {"blue": "#aa0000", "orange": "#00aa00"},
                }
            },
        ),
        live=True,
    )

    assert pixel_colour(window._back.icon()) == "#202020"
    assert pixel_colour(window._up.icon()) == "#202020"
    assert pixel_colour(close.icon()) == "#202020"
    # The sidebar is filled again: its rows are new
    place = window._sidebar.item(window._sidebar.row_of(place_name))
    assert place.icon().pixmap(QSize(28, 28)).toImage().pixelColor(14, 14).name() != tile
    assert (
        QApplication.palette().color(QApplication.palette().ColorRole.WindowText).name()
        == "#202020"
    )
