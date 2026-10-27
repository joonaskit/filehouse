"""The look chosen by hand over the shell's theme: the shell's own values for each
choice, so the app chosen by hand looks as the shell with the same choices."""

import itertools
import json

from PySide6.QtGui import QPalette

from filehouse.integration import appearance, style, theme
from filehouse.integration.appearance import Look
from filehouse.integration.theme_watcher import ThemeWatcher


def shell_theme(**fields):
    return theme.merge(theme.DEFAULTS, {"version": 1, **fields})


def test_no_look_is_the_shells_theme_as_it_is():
    base = shell_theme(colors={"accent": "#30d158"})
    assert appearance.theme_with(base, None) is base


def test_the_light_scheme_has_the_shells_paper_colours():
    made = appearance.theme_with(shell_theme(), Look("light", "blue", "default"))
    assert made["scheme"] == "light"
    assert made["colors"]["surface"] == "#f1ebe1"
    assert made["colors"]["textPrimary"] == "#221f1a"
    # The base is left as it was
    assert shell_theme()["colors"]["surface"] == "#1c1c1e"


def test_dark_over_a_light_shell_is_the_shells_dark():
    light = appearance.theme_with(shell_theme(), Look("light"))
    made = appearance.theme_with(light, Look("dark"))
    assert made["colors"]["surface"] == "#1c1c1e" and made["colors"]["textPrimary"] == "#ffffff"


def test_the_accent_is_the_palettes_with_text_as_the_shell_has_on_it():
    green = appearance.theme_with(shell_theme(), Look(accent="green"))
    assert green["colors"]["accent"] == "#30d158"
    assert green["colors"]["onAccent"] == "#000000"
    blue = appearance.theme_with(shell_theme(), Look(accent="blue"))
    assert blue["colors"]["accent"] == "#0a84ff"
    assert blue["colors"]["onAccent"] == "#ffffff"


def test_the_corners_are_the_shells_radii_scaled_and_rounded_as_it_does():
    def radii(corners):
        return appearance.theme_with(shell_theme(), Look(corners=corners))["radius"]

    assert radii("round") == {"small": 9, "medium": 15, "large": 21}
    assert radii("default") == {"small": 6, "medium": 10, "large": 14}
    assert radii("tight") == {"small": 4, "medium": 6, "large": 8}
    assert radii("square") == {"small": 1, "medium": 2, "large": 3}


def test_the_fonts_and_scales_stay_the_shells():
    base = shell_theme(scale={"ui": 1.1, "font": 1.2}, fonts={"families": ["Cantarell"]})
    made = appearance.theme_with(base, Look("light", "red", "round"))
    assert made["scale"] == {"ui": 1.1, "font": 1.2}
    assert made["fonts"]["families"] == ["Cantarell"]


def test_a_theme_gives_back_the_look_it_was_made_with():
    names = (
        [name for name, _label in appearance.SCHEMES],
        [name for name, _label in appearance.ACCENTS],
        [name for name, _label, _scale in appearance.CORNERS],
    )
    for scheme, accent, corners in itertools.product(*names):
        look = Look(scheme, accent, corners)
        assert appearance.look_of(appearance.theme_with(shell_theme(), look)) == look


def test_the_shells_own_file_gives_its_look():
    # As the author's shell writes it: dark, green, Round
    base = shell_theme(
        colors={"accent": "#30d158", "onAccent": "#000000"},
        radius={"small": 9, "medium": 15, "large": 21},
    )
    assert appearance.look_of(base) == Look("dark", "green", "round")
    # An accent not of the palette is shown as blue
    assert appearance.look_of(shell_theme(colors={"accent": "#123456"})).accent == "blue"


def test_names_not_known_are_the_defaults():
    assert Look("sepia", "teal", "bevelled") == Look("dark", "blue", "default")


def test_the_shell_is_found_by_its_theme_file(tmp_path):
    path = tmp_path / "theme.json"
    assert not appearance.shell_found(path)
    path.write_text("{}")
    assert appearance.shell_found(path)


def test_a_changed_shell_theme_gets_the_look_chosen_over_it(qapp, qtbot, tmp_path):
    path = tmp_path / "quickshell-island" / "theme.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"version": 1}))
    chosen = [Look("light", "orange", "default")]
    watcher = ThemeWatcher(  # noqa: F841 (kept alive)
        qapp, path=path, settle_ms=10, adjust=lambda read: appearance.theme_with(read, chosen[0])
    )
    try:
        with qtbot.waitSignal(style.themed.changed, timeout=5000):
            path.write_text(json.dumps({"version": 1, "colors": {"accent": "#30d158"}}))
        # The shell's green is not shown: orange was chosen over it
        assert qapp.palette().color(QPalette.ColorRole.Highlight).name() == "#ff9f0a"
        assert style.color("surface").name() == "#f1ebe1"

        chosen[0] = None
        with qtbot.waitSignal(style.themed.changed, timeout=5000):
            path.write_text(json.dumps({"version": 1, "colors": {"accent": "#bf5af2"}}))
        assert qapp.palette().color(QPalette.ColorRole.Highlight).name() == "#bf5af2"
    finally:
        style.apply(qapp, theme.load_theme("/nonexistent/theme.json"))
