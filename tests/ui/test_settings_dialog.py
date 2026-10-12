"""The settings dialog: each choice is stored as it is made."""

import pytest

from filehouse.ui import settings
from filehouse.ui.dialogs.settings import SettingsDialog


class Calls:
    def __init__(self):
        self.view, self.hidden, self.cleared = [], [], 0

    def clear(self):
        self.cleared += 1


@pytest.fixture
def dialog(qtbot, home_dir):
    calls = Calls()
    dialog = SettingsDialog(None, calls.view.append, calls.hidden.append, calls.clear)
    qtbot.addWidget(dialog)
    dialog.calls = calls
    return dialog


def test_it_shows_what_is_stored(qtbot, home_dir):
    settings.set_density("compact")
    settings.set_remember_opened(False)
    settings.set_notify(False)
    settings.set_long_job_seconds(30)
    dialog = SettingsDialog(None, print, print, print)
    qtbot.addWidget(dialog)

    assert dialog.density.currentData() == "compact"
    assert dialog.private.isChecked()
    assert not dialog.notify.isChecked()
    assert not dialog.seconds.isEnabled()
    assert dialog.seconds.value() == 30


def test_density_is_stored(dialog):
    dialog.density.setCurrentIndex(dialog.density.findData("compact"))

    assert settings.density() == "compact"


def test_the_view_choices_go_to_the_window(dialog):
    dialog.view.setCurrentIndex(dialog.view.findData(settings.ICONS))
    dialog.hidden.setChecked(True)

    assert dialog.calls.view == [settings.ICONS]
    assert dialog.calls.hidden == [True]


def test_privacy_is_stored_and_followed(dialog):
    dialog.private.setChecked(True)
    assert not settings.remember_opened()

    settings.set_remember_opened(True)
    assert not dialog.private.isChecked()


def test_notifications_are_stored(dialog):
    dialog.seconds.setValue(45)
    dialog.notify.setChecked(False)

    assert settings.long_job_seconds() == 45
    assert not settings.notify()
    assert not dialog.seconds.isEnabled()


def test_clear_is_left_to_the_window(dialog):
    dialog.clear.click()

    assert dialog.calls.cleared == 1


def test_the_details_pane_is_in_the_dialog_and_follows_other_changes(dialog):
    assert dialog.pane.isChecked()

    dialog.pane.setChecked(False)
    assert not settings.details_pane()

    settings.set_details_pane(True)
    assert dialog.pane.isChecked()
