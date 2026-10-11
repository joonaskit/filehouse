import json

from filehouse.integration.theme import DEFAULTS, font_scaled, load_theme, scaled, theme_path


def write(tmp_path, content):
    path = tmp_path / "theme.json"
    path.write_text(content if isinstance(content, str) else json.dumps(content))
    return path


def test_missing_file_gives_the_defaults(tmp_path):
    assert load_theme(tmp_path / "theme.json") == DEFAULTS


def test_broken_file_gives_the_defaults(tmp_path):
    assert load_theme(write(tmp_path, "{not json")) == DEFAULTS
    assert load_theme(write(tmp_path, "[1, 2]")) == DEFAULTS


def test_file_values_overlay_the_defaults(tmp_path):
    theme = load_theme(
        write(tmp_path, {"version": 1, "colors": {"accent": "#30d158"}, "scale": {"ui": 1.15}})
    )

    assert theme["colors"]["accent"] == "#30d158"
    assert theme["colors"]["background"] == DEFAULTS["colors"]["background"]
    assert theme["scale"] == {"ui": 1.15, "font": 1.0}


def test_newer_or_missing_version_gives_the_defaults(tmp_path):
    changed = {"colors": {"accent": "#30d158"}}

    assert load_theme(write(tmp_path, {"version": 2, **changed})) == DEFAULTS
    assert load_theme(write(tmp_path, changed)) == DEFAULTS


def test_unknown_fields_are_kept(tmp_path):
    theme = load_theme(
        write(tmp_path, {"version": 1, "future": 1, "colors": {"palette": {"teal": "#40c8e0"}}})
    )

    assert theme["future"] == 1
    assert theme["colors"]["palette"]["teal"] == "#40c8e0"


def test_palette_is_complete_without_a_file_and_checked_like_other_colours(tmp_path):
    names = {"green", "blue", "orange", "yellow", "red", "purple", "cyan", "indigo"}
    assert set(load_theme(str(tmp_path / "missing.json"))["colors"]["palette"]) == names

    theme = load_theme(
        write(
            tmp_path,
            {"version": 1, "colors": {"palette": {"green": "#112233", "blue": "sky", "red": 7}}},
        )
    )

    palette = theme["colors"]["palette"]
    assert palette["green"] == "#112233"
    # A value that is not a colour falls back to the default, as for any other colour
    assert palette["blue"] == DEFAULTS["colors"]["palette"]["blue"]
    assert palette["red"] == DEFAULTS["colors"]["palette"]["red"]
    assert set(palette) == names


def test_wrong_types_fall_back_per_field(tmp_path):
    theme = load_theme(
        write(
            tmp_path,
            {
                "version": 1,
                "colors": {"accent": 5, "danger": "red", "surface": "#101010"},
                "fonts": {"families": "Cantarell", "displayFamilies": []},
                "radius": "round",
                "scale": {"ui": "big", "font": 0},
            },
        )
    )

    assert theme["colors"]["accent"] == DEFAULTS["colors"]["accent"]
    assert theme["colors"]["danger"] == DEFAULTS["colors"]["danger"]
    assert theme["colors"]["surface"] == "#101010"
    assert theme["fonts"] == DEFAULTS["fonts"]
    assert theme["radius"] == DEFAULTS["radius"]
    assert theme["scale"] == DEFAULTS["scale"]


def test_scaling_matches_the_shell():
    theme = load_theme("/nonexistent")
    theme["scale"] = {"ui": 1.15, "font": 1.15}

    assert scaled(theme, 40) == 46
    assert font_scaled(theme, 12) == 16
    assert font_scaled(theme, 4) == 8


def test_theme_path_follows_xdg_config_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert theme_path() == str(tmp_path / "quickshell-island" / "theme.json")
