"""Finding the terminal and starting it in a folder."""

import os
import stat

import pytest
from gi.repository import GLib

from filehouse.integration import desktop


@pytest.fixture
def kdeglobals():
    path = os.path.join(GLib.get_user_config_dir(), "kdeglobals")
    os.makedirs(os.path.dirname(path), exist_ok=True)

    def write(text):
        with open(path, "w", encoding="utf-8") as file:
            file.write(text)

    yield write
    if os.path.exists(path):
        os.remove(path)


def installed(*names):
    return lambda name: f"/usr/bin/{name}" if name in names else None


def test_the_terminal_chosen_in_plasma_comes_first(kdeglobals, monkeypatch):
    monkeypatch.setenv("TERMINAL", "kitty")
    kdeglobals("[General]\nTerminalApplication=alacritty --some-flag\n")

    assert desktop.terminal(installed("alacritty", "kitty", "konsole")) == "/usr/bin/alacritty"


def test_then_the_one_in_the_environment_then_the_known_ones(kdeglobals, monkeypatch):
    kdeglobals("[General]\nTerminalApplication=gone-terminal\n")
    monkeypatch.setenv("TERMINAL", "kitty")
    assert desktop.terminal(installed("kitty", "konsole")) == "/usr/bin/kitty"

    monkeypatch.delenv("TERMINAL")
    assert desktop.terminal(installed("xterm", "konsole")) == "/usr/bin/konsole"
    assert desktop.terminal(installed("konsole", "xdg-terminal-exec")) == (
        "/usr/bin/xdg-terminal-exec"
    )


def test_no_settings_file_and_no_terminal(monkeypatch):
    monkeypatch.delenv("TERMINAL", raising=False)

    assert desktop.terminal(installed("foot")) == "/usr/bin/foot"
    assert desktop.terminal(installed()) is None


def test_a_broken_settings_file_is_passed_over(kdeglobals, monkeypatch):
    monkeypatch.delenv("TERMINAL", raising=False)
    kdeglobals("not a key file at all [[[")

    assert desktop.terminal(installed("konsole")) == "/usr/bin/konsole"


def test_the_terminal_starts_in_the_folder(tmp_path, monkeypatch):
    # A stand-in terminal that writes down where it was started
    folder = tmp_path / "work here"
    folder.mkdir()
    program = tmp_path / "terminal"
    program.write_text(f"#!/bin/sh\npwd > '{tmp_path}/started'\n")
    program.chmod(program.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(desktop, "default_terminal", lambda: str(program))
    errors = []

    desktop.open_terminal(str(folder), errors.append)

    started = tmp_path / "started"
    for _ in range(200):
        if started.exists() and started.read_text():
            break
        GLib.usleep(10_000)
    assert started.read_text().strip() == str(folder)
    assert errors == []


def test_no_terminal_and_one_that_cannot_start_say_so(tmp_path, monkeypatch):
    errors = []
    monkeypatch.setattr(desktop, "default_terminal", lambda: None)
    desktop.open_terminal(str(tmp_path), errors.append)
    assert errors == ["No terminal was found."]

    monkeypatch.setattr(desktop, "default_terminal", lambda: str(tmp_path / "missing"))
    desktop.open_terminal(str(tmp_path), errors.append)
    assert len(errors) == 2 and errors[1].startswith("Could not start the terminal: ")
