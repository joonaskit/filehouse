"""The glass style: translucent layers with the shell's values, windows that can be glass
filled once, the light on the cards, and no blur where it cannot be had."""

import pytest
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QDialog, QFrame

from filehouse.integration import appearance, blur, style, theme
from filehouse.ui import glass


def themed(look=None, on=True):
    return appearance.theme_with(theme.merge(theme.DEFAULTS, {}), look, glass=on)


@pytest.fixture(autouse=True)
def restore_theme(qapp):
    """The theme is the process's: give it back as it was."""
    before = style._theme
    yield
    style.apply(qapp, before)


def test_offscreen_there_is_no_blur_and_asking_for_one_does_nothing(qtbot, qapp):
    assert not blur.available()
    dialog = QDialog()
    qtbot.addWidget(dialog)
    dialog.show()
    blur.behind(dialog, True)


def test_without_the_library_there_is_no_blur(monkeypatch):
    monkeypatch.setattr(blur, "_LIBRARY", "libnot-there.so.0")
    monkeypatch.setattr(blur, "_calls", None)
    monkeypatch.setattr(blur, "_available", None)
    assert not blur.available()


def test_on_glass_the_layers_let_the_glass_through_and_the_palette_does_not(qapp):
    style.apply(qapp, themed())
    assert style.glass()
    window = style.layer("window")
    # No blur offscreen: the more solid glass, the shell's 80% moved 60% of the way on
    assert window.alphaF() == pytest.approx(0.92, abs=0.01)
    assert window.lightness() < 40
    card = style.layer("card")
    assert (card.red(), card.green(), card.blue()) == (255, 255, 255)
    assert card.alphaF() == pytest.approx(0.07, abs=0.01)
    # What is drawn with no glass under it (tooltips, a combo box's list) is opaque
    palette = qapp.palette()
    for role in (
        QPalette.ColorRole.Window,
        QPalette.ColorRole.Base,
        QPalette.ColorRole.ToolTipBase,
    ):
        assert palette.color(role).alpha() == 255

    # White glass on light
    style.apply(qapp, themed(appearance.Look("light")))
    assert style.layer("window").lightness() > 200
    assert style.layer("card").alphaF() == pytest.approx(0.5, abs=0.01)


def test_solid_is_as_it_was(qapp):
    style.apply(qapp, theme.merge(theme.DEFAULTS, {}))
    before = [style.layer(name).name() for name in ("window", "card", "raised")]
    style.apply(qapp, themed(on=False))
    assert not style.glass()
    assert [style.layer(name).name() for name in ("window", "card", "raised")] == before
    assert style.layer("window").alpha() == 255


@pytest.mark.parametrize("on", [False, True])
def test_a_window_that_can_be_glass_is_filled_once_with_the_window_layer(qtbot, qapp, on):
    style.apply(qapp, themed(on=on))
    dialog = QDialog()
    style.translucent(dialog)
    qtbot.addWidget(dialog)
    dialog.resize(120, 80)
    dialog.show()
    qtbot.waitExposed(dialog)
    pixel = dialog.grab().toImage().pixelColor(60, 40)
    wanted = style.layer("window")
    # Painted twice, the glass would be near opaque
    assert pixel.alpha() == pytest.approx(wanted.alpha(), abs=2)
    assert abs(pixel.red() - wanted.red()) <= 2 and abs(pixel.blue() - wanted.blue()) <= 2


def test_a_window_turns_glass_and_back_while_shown(qtbot, qapp):
    style.apply(qapp, themed(on=False))
    dialog = QDialog()
    style.translucent(dialog)
    qtbot.addWidget(dialog)
    dialog.resize(120, 80)
    dialog.show()
    qtbot.waitExposed(dialog)
    assert dialog.grab().toImage().pixelColor(60, 40).alpha() == 255

    style.apply(qapp, themed(), live=True)
    assert dialog.grab().toImage().pixelColor(60, 40).alpha() < 255


def test_cards_are_lit_on_glass_only(qtbot, qapp):
    style.apply(qapp, themed(on=False))
    card = QFrame()
    qtbot.addWidget(card)
    light = glass.light(card)
    card.resize(200, 100)
    card.show()
    assert not light.isVisible()

    style.apply(qapp, themed(), live=True)
    assert light.isVisible() and light.geometry() == card.rect()
    # The rim is brightest along the top, and the light leaves the middle alone
    image = card.grab().toImage()
    top, side = image.pixelColor(100, 1), image.pixelColor(1, 50)
    assert top.lightness() > side.lightness()

    style.apply(qapp, themed(on=False), live=True)
    assert not light.isVisible()
