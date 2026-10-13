import pytest

from filehouse.integration import motion


@pytest.fixture
def config(tmp_path, monkeypatch):
    """An empty config folder of the user, and one of the system."""
    user = tmp_path / "user"
    system = tmp_path / "system"
    user.mkdir()
    system.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(user))
    monkeypatch.setenv("XDG_CONFIG_DIRS", str(system))
    monkeypatch.setattr(motion, "_read", None)
    return user, system


def test_durations_are_as_written_without_a_setting(config):
    assert motion.factor() == 1.0
    assert motion.ms(motion.SLIDE_MS) == 160


def test_the_desktops_speed_scales_every_duration(config):
    user, _system = config
    (user / "kdeglobals").write_text(
        "[General]\nAnimationDurationFactor=9\n\n[KDE]\nSingleClick=false\n"
        "AnimationDurationFactor=0.5\n\n[KDE][Other]\nAnimationDurationFactor=7\n"
    )

    assert motion.ms(motion.SLIDE_MS) == 80
    assert motion.ms(motion.PROGRESS_MS) == 150


def test_animations_turned_off_are_no_duration(config):
    user, _system = config
    (user / "kdeglobals").write_text("[KDE]\nAnimationDurationFactor[$i]=0\n")

    assert motion.ms(motion.SLIDE_MS) == 0


def test_the_systems_setting_counts_when_the_user_has_none(config):
    user, system = config
    (user / "kdeglobals").write_text("[KDE]\nSingleClick=false\n")
    (system / "kdeglobals").write_text("[KDE]\nAnimationDurationFactor=2\n")

    assert motion.factor() == 2.0


def test_a_setting_that_is_no_number_is_no_setting(config):
    user, _system = config
    (user / "kdeglobals").write_text("[KDE]\nAnimationDurationFactor=fast\n")

    assert motion.factor() == 1.0


def test_a_change_of_the_setting_is_seen_while_the_app_runs(config, monkeypatch):
    user, _system = config
    assert motion.factor() == 1.0
    (user / "kdeglobals").write_text("[KDE]\nAnimationDurationFactor=0\n")
    assert motion.factor() == 1.0  # read a moment ago

    now = motion.time.monotonic()
    monkeypatch.setattr(motion.time, "monotonic", lambda: now + 60)
    assert motion.factor() == 0.0
