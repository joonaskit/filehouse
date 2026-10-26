"""The rules of a folder from the window: the dialog with its preview, and "Run rules
now" (#87)."""

import os
import time

import pytest
from shiboken6 import isValid
from ui_helpers import TIMEOUT, gfile

from filehouse.core import automations as auto
from filehouse.core.automations import ActionKind, ConditionKind
from filehouse.core.operations import JobKind, JobState
from filehouse.ui import settings
from filehouse.ui.dialogs.rules import FolderRulesDialog, RunRulesDialog
from filehouse.ui.main_window import MainWindow


def settled(folder, *names):
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / name).write_text(name)
        os.utime(folder / name, (time.time() - 60, time.time() - 60))
    return folder


def pdfs_to(path, name="pdfs"):
    return auto.Rule(
        name,
        (auto.Condition(ConditionKind.NAME, "glob", "*.pdf"),),
        (auto.Action(ActionKind.MOVE, str(path)),),
    )


@pytest.fixture
def open_window(qtbot, jobs):
    windows = []

    def open_at(path):
        window = MainWindow(gfile(path), jobs=jobs)
        windows.append(window)
        window.show()
        qtbot.waitUntil(lambda: not window._model.loading, timeout=TIMEOUT)
        return window

    yield open_at
    jobs.cancel_all()
    qtbot.waitUntil(lambda: jobs.active_count() == 0, timeout=TIMEOUT)
    for window in windows:
        if isValid(window):
            window.close()


def checked(qtbot, dialog):
    qtbot.waitUntil(lambda: not dialog.busy and dialog._plan is not None, timeout=TIMEOUT)
    return dialog


def preview_rows(dialog):
    return [
        (dialog.preview.topLevelItem(row).text(0), dialog.preview.topLevelItem(row).text(1))
        for row in range(dialog.preview.topLevelItemCount())
    ]


def test_a_new_rule_is_filled_in_previewed_and_saved(qtbot, jobs, tmp_path):
    folder = settled(tmp_path / "in", "a.pdf", "b.txt")
    dialog = FolderRulesDialog(None, gfile(folder), jobs)
    qtbot.addWidget(dialog)
    checked(qtbot, dialog)
    assert not dialog.save_and_run.isEnabled()

    dialog._add_rule()
    checked(qtbot, dialog)
    # As it comes, the rule cannot work: it says why, and cannot be saved
    assert dialog.problems.isVisibleTo(dialog) and dialog.problems.text()
    assert not dialog.save.isEnabled()

    dialog.name.setText("PDFs away")
    condition = dialog._conditions.itemAt(0).widget()
    condition.text.setText("*.pdf")
    action = dialog._actions.itemAt(0).widget()
    action.value.setText(str(tmp_path / "Invoices"))
    checked(qtbot, dialog)

    assert dialog.problems.text() == ""
    assert preview_rows(dialog) == [("a.pdf", f"Move to {tmp_path / 'Invoices'} (new folder)")]
    assert "1 file" in dialog.status.text()
    assert dialog.save.isEnabled() and dialog.save_and_run.isEnabled()

    dialog.save.click()

    (rule,) = settings.folder_rules(gfile(folder).get_uri())
    assert rule.name == "PDFs away"
    assert rule.actions == (auto.Action(ActionKind.MOVE, str(tmp_path / "Invoices")),)
    # Saving does nothing to the files
    assert sorted(os.listdir(folder)) == ["a.pdf", "b.txt"]


def test_save_and_run_now_hands_the_preview_to_the_jobs(qtbot, jobs, tmp_path):
    folder = settled(tmp_path / "in", "a.pdf")
    settings.set_folder_rules(gfile(folder).get_uri(), [pdfs_to(tmp_path / "out")])
    dialog = FolderRulesDialog(None, gfile(folder), jobs)
    qtbot.addWidget(dialog)
    checked(qtbot, dialog)

    with qtbot.waitSignal(jobs.idle, timeout=TIMEOUT):
        dialog.save_and_run.click()

    (job,) = jobs.jobs
    assert job.kind is JobKind.RULES and job.state is JobState.DONE
    assert os.listdir(tmp_path / "out") == ["a.pdf"]


def test_a_rule_can_be_switched_off_and_moved(qtbot, jobs, tmp_path):
    folder = settled(tmp_path / "in", "a.pdf")
    uri = gfile(folder).get_uri()
    settings.set_folder_rules(
        uri, [pdfs_to(tmp_path / "one", "first"), pdfs_to(tmp_path / "two", "second")]
    )
    dialog = FolderRulesDialog(None, gfile(folder), jobs)
    qtbot.addWidget(dialog)
    checked(qtbot, dialog)
    assert preview_rows(dialog)[0][1].startswith(f"Move to {tmp_path / 'one'}")

    dialog.list.setCurrentRow(1)
    dialog.up.click()
    checked(qtbot, dialog)
    assert preview_rows(dialog)[0][1].startswith(f"Move to {tmp_path / 'two'}")

    from PySide6.QtCore import Qt

    dialog.list.item(0).setCheckState(Qt.CheckState.Unchecked)
    checked(qtbot, dialog)
    assert preview_rows(dialog)[0][1].startswith(f"Move to {tmp_path / 'one'}")

    dialog.save.click()
    rules = settings.folder_rules(uri)
    assert [(rule.name, rule.enabled) for rule in rules] == [("second", False), ("first", True)]


def test_removing_the_last_rule_forgets_the_folder(qtbot, jobs, tmp_path):
    folder = settled(tmp_path / "in")
    uri = gfile(folder).get_uri()
    settings.set_folder_rules(uri, [pdfs_to(tmp_path / "out")])
    dialog = FolderRulesDialog(None, gfile(folder), jobs)
    qtbot.addWidget(dialog)

    dialog._remove_rule()
    checked(qtbot, dialog)
    dialog.save.click()

    assert settings.all_folder_rules() == {}


def test_the_note_says_when_rules_cannot_run_without_a_window(qtbot, jobs, tmp_path):
    staying = FolderRulesDialog(None, gfile(tmp_path), jobs, stay_running=True)
    ending = FolderRulesDialog(None, gfile(tmp_path), jobs, stay_running=False)
    qtbot.addWidget(staying)
    qtbot.addWidget(ending)

    assert "last window closes" not in staying.note.text()
    assert "last window closes" in ending.note.text()


def test_run_rules_now_shows_what_happens_first(qtbot, jobs, tmp_path):
    folder = settled(tmp_path / "in", "a.pdf", "b.txt")
    settings.set_folder_rules(gfile(folder).get_uri(), [pdfs_to(tmp_path / "out")])
    dialog = RunRulesDialog(None, gfile(folder), jobs)
    qtbot.addWidget(dialog)
    qtbot.waitUntil(dialog.run.isEnabled, timeout=TIMEOUT)

    assert preview_rows(dialog) == [("a.pdf", f"Move to {tmp_path / 'out'} (new folder)")]
    assert os.listdir(folder) and jobs.jobs == []

    with qtbot.waitSignal(jobs.idle, timeout=TIMEOUT):
        dialog.run.click()

    assert os.listdir(tmp_path / "out") == ["a.pdf"]


def test_the_folder_menu_offers_rules_and_running_them_once_there_are_some(
    qtbot, open_window, tmp_path
):
    folder = settled(tmp_path / "in")
    window = open_window(folder)
    actions = window._actions
    menu = window._menus.for_entry(None).actions()

    assert actions.folder_rules in menu and actions.run_rules in menu
    assert actions.folder_rules.isEnabled()
    assert not actions.run_rules.isVisible()

    settings.set_folder_rules(gfile(folder).get_uri(), [pdfs_to(tmp_path / "out")])

    assert actions.run_rules.isVisible()
    dialog = window.open_folder_rules()
    assert isinstance(dialog, FolderRulesDialog) and dialog.list.count() == 1
    dialog.reject()


def test_rules_are_not_offered_in_the_trash(qtbot, open_window, tmp_path):
    from filehouse.core import locations

    window = open_window(tmp_path)
    window.navigate(locations.trash())
    qtbot.waitUntil(lambda: window._pane.in_trash, timeout=TIMEOUT)
    window._update_state()

    assert not window._actions.folder_rules.isEnabled()


def test_the_drop_downs_of_a_condition_are_never_squeezed(qtbot, jobs, tmp_path):
    dialog = FolderRulesDialog(None, gfile(tmp_path), jobs)
    qtbot.addWidget(dialog)
    dialog._add_rule()
    dialog.resize(dialog.minimumSize())
    dialog.show()
    row = dialog._conditions.itemAt(0).widget()

    row.kind.setCurrentIndex(row.kind.findData(ConditionKind.AGE))
    qtbot.waitUntil(lambda: row.mode.width() >= row.mode.sizeHint().width(), timeout=TIMEOUT)

    metrics = row.mode.fontMetrics()
    longest = max(metrics.horizontalAdvance(row.mode.itemText(i)) for i in range(row.mode.count()))
    assert row.mode.width() > longest
