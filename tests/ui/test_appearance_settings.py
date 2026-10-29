"""The settings' Appearance: the shell's look followed, or one chosen by hand, applied at
once and remembered (#30)."""

import json

import pytest
from PySide6.QtGui import QPalette

from filehouse.integration import appearance, style
from filehouse.integration.appearance import Look
from filehouse.ui import settings
from filehouse.ui.dialogs.settings import SettingsDialog

# As the author's shell writes it: dark, green, Round
SHELL = {
    "version": 1,
    "colors": {"accent": "#30d158", "onAccent": "#000000"},
    "radius": {"small": 9, "medium": 15, "large": 21},
}


@pytest.fixture
def shell(tmp_path, monkeypatch):
    """The shell's theme file, where the app looks for it."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = tmp_path / "quickshell-island" / "theme.json"
    path.parent.mkdir()
    path.write_text(json.dumps(SHELL))
    return path


@pytest.fixture(autouse=True)
def restore_theme(qapp):
    """The theme is the process's: give it back as it was."""
    before = style._theme
    yield
    style.apply(qapp, before)


def open_dialog(qtbot, qapp):
    appearance.apply(qapp, settings.chosen_look(), settings.glass(), live=False)
    dialog = SettingsDialog(None, print, print)
    qtbot.addWidget(dialog)
    return dialog


def accent(qapp):
    return qapp.palette().color(QPalette.ColorRole.Highlight).name()


def test_the_settings_are_in_tabs_with_the_appearance_first(qtbot, qapp):
    dialog = open_dialog(qtbot, qapp)

    tabs = [dialog.tabs.tabText(index) for index in range(dialog.tabs.count())]
    assert tabs == ["Appearance", "Folders", "General"]
    assert dialog.tabs.widget(0).isAncestorOf(dialog.density)
    assert dialog.tabs.widget(1).isAncestorOf(dialog.rules)
    assert dialog.tabs.widget(2).isAncestorOf(dialog.seconds)
    assert dialog.tabs.widget(2).isAncestorOf(dialog.clear)


def test_with_the_shell_its_look_is_followed_and_shown_greyed(qtbot, qapp, shell):
    dialog = open_dialog(qtbot, qapp)
    assert dialog.follow.isVisibleTo(dialog) and dialog.follow.isChecked()
    assert (dialog.scheme.value(), dialog.accent.value(), dialog.corners.value()) == (
        "dark",
        "green",
        "round",
    )
    assert not dialog.scheme.isEnabled() and not dialog.accent.isEnabled()
    assert not dialog.corners.isEnabled()
    assert accent(qapp) == "#30d158"


def test_choosing_by_hand_starts_from_the_shells_look_and_applies_at_once(qtbot, qapp, shell):
    dialog = open_dialog(qtbot, qapp)
    dialog.follow.setChecked(False)
    assert not settings.follow_shell()
    assert settings.look() == Look("dark", "green", "round")
    # Nothing changed by turning it off
    assert accent(qapp) == "#30d158" and style.radius("medium") == style.px(15)
    assert dialog.scheme.isEnabled() and dialog.accent.isEnabled()

    dialog.scheme.button("light").click()
    dialog.accent.dot("orange").click()
    dialog.corners.button("tight").click()
    assert settings.look() == Look("light", "orange", "tight")
    assert style.color("surface").name() == "#f1ebe1"
    assert accent(qapp) == "#ff9f0a"
    assert style.radius("medium") == style.px(6)
    assert dialog.accent.name.text() == "Orange"


def test_the_look_chosen_is_kept_over_the_shells_changes(qtbot, qapp, shell):
    settings.set_follow_shell(False)
    settings.set_look(Look("light", "purple", "square"))
    dialog = open_dialog(qtbot, qapp)
    assert accent(qapp) == "#bf5af2"
    assert not dialog.follow.isChecked() and dialog.scheme.value() == "light"

    # Following again is the shell's look, and shows it
    dialog.follow.setChecked(True)
    assert accent(qapp) == "#30d158" and style.color("surface").name() == "#1c1c1e"
    assert dialog.accent.value() == "green" and not dialog.accent.isEnabled()


def test_while_following_the_shells_changes_show(qtbot, qapp, shell):
    dialog = open_dialog(qtbot, qapp)
    shell.write_text(json.dumps({**SHELL, "colors": {"accent": "#ff453a"}}))
    appearance.apply(qapp, settings.chosen_look())
    assert dialog.accent.value() == "red"


def test_without_the_shell_the_look_is_chosen_here(qtbot, qapp):
    assert not appearance.shell_found()
    dialog = open_dialog(qtbot, qapp)
    assert not dialog.follow.isVisibleTo(dialog)
    assert dialog.scheme.isEnabled() and dialog.accent.isEnabled()
    assert (dialog.scheme.value(), dialog.accent.value(), dialog.corners.value()) == (
        "dark",
        "blue",
        "default",
    )
    dialog.accent.dot("cyan").click()
    assert accent(qapp) == "#64d2ff" and settings.chosen_look() == Look(accent="cyan")


def test_a_closed_dialog_no_longer_follows_the_theme(qtbot, qapp):
    dialog = open_dialog(qtbot, qapp)
    dialog.close()
    # Would raise in _show_look on a dialog that is gone, were it still connected
    appearance.apply(qapp, Look(accent="red"))
    assert accent(qapp) == "#ff453a"


def test_glass_is_chosen_here_applies_at_once_and_stays_while_following(qtbot, qapp, shell):
    dialog = open_dialog(qtbot, qapp)
    assert dialog.glass.value() == "solid" and not style.glass()
    # The shell does not tell its style: the choice is the app's, followed or not
    assert dialog.glass.isEnabled() and not dialog.scheme.isEnabled()

    dialog.glass.button("glass").click()
    assert settings.glass() and style.glass()
    # Still the shell's look
    assert accent(qapp) == "#30d158"

    dialog.glass.button("solid").click()
    assert not settings.glass() and not style.glass()


def test_glass_is_remembered(qtbot, qapp):
    settings.set_glass(True)
    dialog = open_dialog(qtbot, qapp)
    assert dialog.glass.value() == "glass"
