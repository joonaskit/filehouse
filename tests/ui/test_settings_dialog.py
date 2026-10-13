"""The settings dialog: each choice is stored as it is made."""

import pytest

from filehouse.integration import autostart
from filehouse.ui import settings
from filehouse.ui.dialogs.settings import SettingsDialog


class Calls:
    def __init__(self):
        self.hidden, self.cleared = [], 0

    def clear(self):
        self.cleared += 1


@pytest.fixture
def dialog(qtbot, home_dir):
    calls = Calls()
    dialog = SettingsDialog(None, calls.hidden.append, calls.clear)
    qtbot.addWidget(dialog)
    dialog.calls = calls
    return dialog


def test_it_shows_what_is_stored(qtbot, home_dir):
    settings.set_density("compact")
    settings.set_remember_opened(False)
    settings.set_notify(False)
    settings.set_long_job_seconds(30)
    dialog = SettingsDialog(None, print, print)
    qtbot.addWidget(dialog)

    assert dialog.density.currentData() == "compact"
    assert dialog.private.isChecked()
    assert not dialog.notify.isChecked()
    assert not dialog.seconds.isEnabled()
    assert dialog.seconds.value() == 30


def test_density_is_stored(dialog):
    dialog.density.setCurrentIndex(dialog.density.findData("compact"))

    assert settings.density() == "compact"


def test_hidden_files_go_to_the_window(dialog):
    dialog.hidden.setChecked(True)

    assert dialog.calls.hidden == [True]


def test_the_view_and_the_sort_order_of_folders_are_stored(dialog):
    assert dialog.sort.currentText() == "Name"
    assert dialog.per_folder.isChecked()

    dialog.view.setCurrentIndex(dialog.view.findData(settings.ICONS))
    dialog.sort.setCurrentIndex(dialog.sort.findText("Modified, newest first"))
    dialog.per_folder.setChecked(False)

    assert settings.view_mode() == settings.ICONS
    assert settings.sort() == ("modified", True)
    assert not settings.per_folder()


def test_a_sort_the_list_does_not_offer_is_shown_as_it_is(qtbot, home_dir):
    settings.set_sort("permissions", False)
    dialog = SettingsDialog(None, print, print)
    qtbot.addWidget(dialog)

    assert dialog.sort.currentText() == "Permissions"
    assert settings.sort() == ("permissions", False)
    dialog.sort.setCurrentIndex(dialog.sort.findText("Size, largest first"))
    assert settings.sort() == ("size", True)


def test_starting_with_the_tabs_of_last_time_is_stored(dialog):
    assert dialog.restore.isChecked()

    dialog.restore.setChecked(False)

    assert not settings.restore_tabs()


def test_staying_without_a_window_is_stored_and_told(dialog):
    told = []
    settings.changes.stay_running_changed.connect(told.append)
    try:
        assert dialog.stay.isChecked()

        dialog.stay.setChecked(False)
    finally:
        settings.changes.stay_running_changed.disconnect(told.append)

    assert not settings.stay_running()
    assert told == [False]


def test_starting_at_a_login_is_an_entry_in_the_autostart_folder(dialog):
    assert not dialog.login.isChecked()

    dialog.login.setChecked(True)
    assert autostart.enabled()

    dialog.login.setChecked(False)
    assert not autostart.enabled()


def test_only_a_process_that_stays_is_started_at_a_login(dialog):
    dialog.login.setChecked(True)

    dialog.stay.setChecked(False)

    assert not dialog.login.isChecked() and not dialog.login.isEnabled()
    assert not autostart.enabled()
    dialog.stay.setChecked(True)
    assert dialog.login.isEnabled() and not dialog.login.isChecked()


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
