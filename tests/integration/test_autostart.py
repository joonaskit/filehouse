"""Starting with the session: an entry in the autostart folder, written and taken away."""

import os

import pytest

from filehouse.integration import autostart


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def test_it_is_off_until_it_is_turned_on(config):
    assert not autostart.enabled()

    assert autostart.set_enabled(True)

    assert autostart.enabled()
    entry = (config / "autostart" / "filehouse-background.desktop").read_text()
    assert "Type=Application" in entry
    # Without a window, as the bus starts it for another app
    assert [line for line in entry.splitlines() if line.startswith("Exec=")][0].endswith(
        " --service"
    )


def test_turning_it_off_takes_the_entry_away(config):
    autostart.set_enabled(True)

    assert autostart.set_enabled(False)

    assert not autostart.enabled()
    assert not (config / "autostart" / "filehouse-background.desktop").exists()
    # And again, with nothing there
    assert autostart.set_enabled(False)


def test_the_entry_runs_the_command_this_process_was_started_with(config, tmp_path, monkeypatch):
    command = tmp_path / "my bin" / "filehouse"
    command.parent.mkdir()
    command.write_text("#!/bin/sh\n")
    command.chmod(0o755)
    monkeypatch.setattr("sys.argv", [str(command), "/somewhere"])

    autostart.set_enabled(True)

    entry = (config / "autostart" / "filehouse-background.desktop").read_text()
    assert f'Exec="{command}" --service' in entry


def test_started_some_other_way_it_is_the_command_on_the_path(config, monkeypatch):
    monkeypatch.setattr("sys.argv", ["/usr/lib/python3/site-packages/filehouse/__main__.py"])
    monkeypatch.setattr(autostart.shutil, "which", lambda name: None)

    assert autostart.command() == "filehouse"


def test_a_folder_that_cannot_be_written_leaves_it_off(config):
    (config / "autostart").write_text("a file where the folder would be")

    assert not autostart.set_enabled(True)
    assert not autostart.enabled()


def test_what_is_special_in_a_command_is_escaped():
    assert autostart._quoted('/a "b"/$c') == '"/a \\"b\\"/\\$c"'
    assert os.path.basename(autostart.path()) == "filehouse-background.desktop"
