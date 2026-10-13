"""A dialog over another app's window: only where it can be done, and never an error."""

import pytest
from PySide6.QtWidgets import QWidget

from filehouse.integration import parent_window


@pytest.mark.parametrize("handle", ["", "x11:1a", "wayland:abc", "wayland:", "nonsense", "odd:1"])
def test_where_it_cannot_be_done_the_window_is_one_of_its_own(qtbot, handle):
    widget = QWidget()
    qtbot.addWidget(widget)
    widget.winId()

    # The tests run on no desktop at all: neither X11 nor Wayland
    assert parent_window.attach(widget.windowHandle(), handle) is False


def test_a_failure_on_the_way_is_not_the_dialogs_problem(qtbot, monkeypatch):
    def broken(_window, _handle):
        raise OSError("no such library")

    monkeypatch.setattr(parent_window.QGuiApplication, "platformName", lambda: "wayland")
    monkeypatch.setattr(parent_window, "_attach_wayland", broken)
    widget = QWidget()
    qtbot.addWidget(widget)
    widget.winId()

    assert parent_window.attach(widget.windowHandle(), "wayland:abc") is False
