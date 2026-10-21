"""The dialog other apps open and save with, given requests as the portal makes them."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView
from shiboken6 import isValid
from ui_helpers import TIMEOUT, gfile

from filehouse.core.filters import GLOB, MIME, Filter
from filehouse.integration import portal
from filehouse.integration.portal import Choice, Request
from filehouse.ui import chooser as chooser_module
from filehouse.ui import settings
from filehouse.ui.chooser import KEEP_BOTH, REPLACE, Chooser


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "work"
    (root / "docs" / "old").mkdir(parents=True)
    (root / "pictures").mkdir()
    (root / "report.txt").write_text("top")
    (root / "notes.md").write_text("notes")
    (root / "photo.PNG").write_text("not a picture")
    (root / ".hidden.txt").write_text("hidden")
    (root / "docs" / "summary.txt").write_text("b")
    return root


class Asked:
    """A request and what it was answered with: (response, results), or nothing yet."""

    def __init__(self, kind, **options):
        self.answers = []
        self.request = Request(kind, "org.example.App", "", options.pop("title", ""), self._answer)
        for name, value in options.items():
            assert hasattr(self.request, name), name
            setattr(self.request, name, value)

    def _answer(self, response, results):
        self.answers.append((response, {key: value.unpack() for key, value in results.items()}))

    @property
    def uris(self):
        ((response, results),) = self.answers
        assert response == portal.SUCCESS
        return results["uris"]


@pytest.fixture
def open_dialog(qtbot):
    made = []

    def make(asked, show=True):
        dialog = Chooser(asked.request)
        made.append(dialog)
        if show:
            dialog.show()
        qtbot.waitUntil(lambda: not dialog._pane.model.loading, timeout=TIMEOUT)
        return dialog

    yield make
    for dialog in made:
        if isValid(dialog):
            dialog.close()


def shown(dialog):
    return sorted(info.name for info in dialog._pane.shown_infos())


def select(qtbot, dialog, *names):
    picked = dialog._view.select_where(lambda info: info.name in names)
    assert picked == len(names)


def uri(path):
    return gfile(path).get_uri()


# Opening


def test_it_opens_in_the_folder_the_app_names_with_the_apps_title(qtbot, open_dialog, tree):
    asked = Asked(portal.OPEN, folder=gfile(tree), title="Import", accept_label="_Import")

    dialog = open_dialog(asked)

    assert dialog.windowTitle() == "Import"
    assert dialog.accept_button.text() == "&Import"
    assert shown(dialog) == ["docs", "notes.md", "photo.PNG", "pictures", "report.txt"]
    assert dialog._path_bar.crumbs()[-1] == "work"
    # Nothing is selected, so there is nothing to open yet
    assert not dialog.accept_button.isEnabled()
    assert dialog.name is None and dialog.filters is None


def test_the_chosen_file_is_the_answer_and_the_dialog_is_gone(qtbot, open_dialog, tree):
    asked = Asked(portal.OPEN, folder=gfile(tree))
    dialog = open_dialog(asked)

    select(qtbot, dialog, "report.txt")
    assert dialog.accept_button.isEnabled()
    dialog.accept_button.click()

    assert asked.uris == [uri(tree / "report.txt")]
    assert not dialog.isVisible()
    # Where the next dialog starts, for an app that names no folder
    assert settings.chooser_folder() == uri(tree)


def test_a_double_click_on_a_file_chooses_it_and_on_a_folder_goes_into_it(qtbot, open_dialog, tree):
    asked = Asked(portal.OPEN, folder=gfile(tree))
    dialog = open_dialog(asked)
    infos = {info.name: info for info in dialog._pane.shown_infos()}

    dialog._view.item_activated.emit(infos["docs"])
    qtbot.waitUntil(lambda: shown(dialog) == ["old", "summary.txt"], timeout=TIMEOUT)
    assert not asked.answers

    summary = next(info for info in dialog._pane.shown_infos() if info.name == "summary.txt")
    dialog._view.item_activated.emit(summary)

    assert asked.uris == [uri(tree / "docs" / "summary.txt")]


def test_the_button_goes_into_a_selected_folder_instead_of_opening_it(qtbot, open_dialog, tree):
    asked = Asked(portal.OPEN, folder=gfile(tree))
    dialog = open_dialog(asked)

    select(qtbot, dialog, "docs")
    dialog.accept_button.click()

    qtbot.waitUntil(lambda: shown(dialog) == ["old", "summary.txt"], timeout=TIMEOUT)
    assert not asked.answers and dialog.isVisible()


def test_one_file_at_a_time_unless_several_are_asked_for(qtbot, open_dialog, tree):
    single = QAbstractItemView.SelectionMode.SingleSelection
    one = open_dialog(Asked(portal.OPEN, folder=gfile(tree)))
    asked = Asked(portal.OPEN, folder=gfile(tree), multiple=True)
    several = open_dialog(asked)

    assert one._view._items().selectionMode() == single
    assert several._view._items().selectionMode() != single

    select(qtbot, several, "report.txt", "notes.md", "docs")
    several.accept_button.click()

    # The files of the selection: a folder among them is not a file to open
    assert asked.uris == [uri(tree / "notes.md"), uri(tree / "report.txt")]


def test_closing_it_is_an_answer_too(qtbot, open_dialog, tree):
    by_button = Asked(portal.OPEN, folder=gfile(tree))
    by_key = Asked(portal.OPEN, folder=gfile(tree))
    by_window = Asked(portal.OPEN, folder=gfile(tree))

    open_dialog(by_button).cancel_button.click()
    dialog = open_dialog(by_key)
    qtbot.keyClick(dialog, Qt.Key.Key_Escape)
    open_dialog(by_window).close()

    for asked in (by_button, by_key, by_window):
        assert asked.answers == [(portal.CANCELLED, {})]


def test_taken_back_by_the_app_it_closes(qtbot, open_dialog, tree):
    asked = Asked(portal.OPEN, folder=gfile(tree))
    dialog = open_dialog(asked)

    asked.request.on_closed()

    assert not dialog.isVisible()


def test_return_in_the_list_does_not_choose_what_happens_to_be_selected(qtbot, open_dialog, tree):
    asked = Asked(portal.OPEN, folder=gfile(tree))
    dialog = open_dialog(asked)
    select(qtbot, dialog, "report.txt")

    # Return in the path field, with a file selected in the list
    dialog._path_bar.edit()
    dialog._path_bar._edit.setText(str(tree / "docs"))
    qtbot.keyClick(dialog._path_bar._edit, Qt.Key.Key_Return)

    qtbot.waitUntil(lambda: shown(dialog) == ["old", "summary.txt"], timeout=TIMEOUT)
    assert not asked.answers


# Filters and the app's own questions

TEXT = Filter("Text", ((GLOB, "*.txt"), (GLOB, "*.md")))
PICTURES = Filter("Pictures", ((GLOB, "*.png"),))
PLAIN = Filter("Plain text", ((MIME, "text/plain"),))


def test_only_the_files_of_the_filter_are_shown_and_folders_always(qtbot, open_dialog, tree):
    asked = Asked(
        portal.OPEN, folder=gfile(tree), filters=[TEXT, PICTURES], current_filter=PICTURES
    )
    dialog = open_dialog(asked)

    assert dialog.filters.currentText() == "Pictures"
    assert shown(dialog) == ["docs", "photo.PNG", "pictures"]

    dialog.filters.setCurrentIndex(0)
    assert shown(dialog) == ["docs", "notes.md", "pictures", "report.txt"]

    select(qtbot, dialog, "notes.md")
    dialog.accept_button.click()
    ((_response, results),) = asked.answers
    # The app is told which filter it was
    assert results["current_filter"] == ("Text", [(GLOB, "*.txt"), (GLOB, "*.md")])


def test_without_a_current_filter_the_first_is_used(qtbot, open_dialog, tree):
    dialog = open_dialog(Asked(portal.OPEN, folder=gfile(tree), filters=[PLAIN, PICTURES]))

    assert dialog.filters.currentText() == "Plain text"
    assert "photo.PNG" not in shown(dialog) and "report.txt" in shown(dialog)


def test_the_apps_own_questions_are_asked_and_answered(qtbot, open_dialog, tree):
    asked = Asked(
        portal.OPEN,
        folder=gfile(tree),
        choices=[
            Choice("encoding", "Encoding", (("utf8", "UTF-8"), ("latin", "Latin 1")), "latin"),
            Choice("readonly", "Read only", (), "false"),
            Choice("again", "Open again", (), "true"),
        ],
    )
    dialog = open_dialog(asked)

    assert dialog.choices["encoding"].currentText() == "Latin 1"
    assert not dialog.choices["readonly"].isChecked() and dialog.choices["again"].isChecked()

    dialog.choices["encoding"].setCurrentIndex(0)
    dialog.choices["readonly"].setChecked(True)
    select(qtbot, dialog, "report.txt")
    dialog.accept_button.click()

    ((_response, results),) = asked.answers
    assert results["choices"] == [
        ("again", "true"),
        ("encoding", "utf8"),
        ("readonly", "true"),
    ]


# Folders


def test_choosing_a_folder_shows_folders_only_and_says_which_it_would_be(qtbot, open_dialog, tree):
    asked = Asked(portal.OPEN, folder=gfile(tree), directory=True, filters=[TEXT])
    dialog = open_dialog(asked)

    assert shown(dialog) == ["docs", "pictures"]
    assert dialog.filters is None
    # With nothing selected it is the folder that is open
    assert dialog.accept_button.isEnabled() and dialog.accept_button.text() == "Select"
    assert dialog._message.text() == "The folder “work”"

    select(qtbot, dialog, "docs")
    assert dialog._message.text() == "The folder “docs”"
    dialog.accept_button.click()

    assert asked.uris == [uri(tree / "docs")]


def test_the_open_folder_is_chosen_when_none_is_selected(qtbot, open_dialog, tree):
    asked = Asked(portal.OPEN, folder=gfile(tree / "docs"), directory=True)
    dialog = open_dialog(asked)

    dialog.accept_button.click()

    assert asked.uris == [uri(tree / "docs")]


# Saving


def test_saving_offers_the_name_ready_to_be_typed_over(qtbot, open_dialog, tree):
    asked = Asked(portal.SAVE, folder=gfile(tree), name="draft.txt")
    dialog = open_dialog(asked)

    assert dialog.name.text() == "draft.txt"
    assert dialog.name.selectedText() == "draft"
    assert dialog.accept_button.text() == "Save" and dialog.accept_button.isEnabled()

    dialog.name.setText("  ")
    assert not dialog.accept_button.isEnabled()

    dialog.name.setText("final.txt")
    qtbot.keyClick(dialog.name, Qt.Key.Key_Return)

    assert asked.uris == [uri(tree / "final.txt")]
    # Nothing is made by the dialog: the app writes the file
    assert not (tree / "final.txt").exists()


def test_saving_a_file_again_starts_at_that_file(qtbot, open_dialog, tree):
    asked = Asked(portal.SAVE, file=gfile(tree / "docs" / "summary.txt"))
    dialog = open_dialog(asked)

    assert shown(dialog) == ["old", "summary.txt"]
    assert dialog.name.text() == "summary.txt"
    qtbot.waitUntil(
        lambda: [info.name for info in dialog._view.selected_infos()] == ["summary.txt"],
        timeout=TIMEOUT,
    )


def test_saving_over_a_file_asks_first(qtbot, open_dialog, tree, monkeypatch):
    questions = []
    answers = [None, REPLACE]

    def ask(_parent, names, folder, keep_both=False):
        questions.append((names, folder, keep_both))
        return answers.pop(0)

    monkeypatch.setattr(chooser_module, "ask_to_replace", ask)
    asked = Asked(portal.SAVE, folder=gfile(tree), name="report.txt")
    dialog = open_dialog(asked)

    dialog.accept_button.click()
    # Not replaced: nothing is answered, and the dialog is still there
    assert not asked.answers and dialog.isVisible()

    dialog.accept_button.click()
    assert asked.uris == [uri(tree / "report.txt")]
    assert questions == [(["report.txt"], "work", False)] * 2
    assert (tree / "report.txt").read_text() == "top"


def test_a_file_clicked_in_the_list_gives_its_name(qtbot, open_dialog, tree):
    dialog = open_dialog(Asked(portal.SAVE, folder=gfile(tree), name="draft.txt"))

    select(qtbot, dialog, "notes.md")
    assert dialog.name.text() == "notes.md"

    # A folder does not: it is not what is being named
    select(qtbot, dialog, "docs")
    assert dialog.name.text() == "notes.md"


def test_a_folder_typed_as_the_name_is_gone_into(qtbot, open_dialog, tree):
    asked = Asked(portal.SAVE, folder=gfile(tree), name="draft.txt")
    dialog = open_dialog(asked)

    dialog.name.setText("docs")
    dialog.accept_button.click()

    qtbot.waitUntil(lambda: shown(dialog) == ["old", "summary.txt"], timeout=TIMEOUT)
    assert not asked.answers and dialog.name.text() == ""

    # And a path from here names a file in a folder below
    dialog.name.setText("old/new.txt")
    dialog.accept_button.click()
    assert asked.uris == [uri(tree / "docs" / "old" / "new.txt")]


def test_a_name_in_a_folder_that_is_not_there_is_not_saved(qtbot, open_dialog, tree):
    asked = Asked(portal.SAVE, folder=gfile(tree), name="draft.txt")
    dialog = open_dialog(asked)

    dialog.name.setText("nowhere/new.txt")
    dialog.accept_button.click()

    assert not asked.answers
    assert "nowhere" in dialog._message.text()
    # Said until something else is typed
    dialog.name.setText("new.txt")
    assert dialog._message.text() == ""


def test_a_full_path_as_the_name_is_that_file(qtbot, open_dialog, tree):
    asked = Asked(portal.SAVE, folder=gfile(tree), name="draft.txt")
    dialog = open_dialog(asked)

    dialog.name.setText(str(tree / "pictures" / "new.png"))
    dialog.accept_button.click()

    assert asked.uris == [uri(tree / "pictures" / "new.png")]


# Saving several files into one folder


def test_several_files_are_saved_in_the_folder_chosen(qtbot, open_dialog, tree):
    asked = Asked(portal.SAVE_FILES, folder=gfile(tree), names=["a.txt", "b.txt"])
    dialog = open_dialog(asked)

    assert shown(dialog) == ["docs", "pictures"] and dialog.name is None
    select(qtbot, dialog, "pictures")
    dialog.accept_button.click()

    assert asked.uris == [uri(tree / "pictures" / "a.txt"), uri(tree / "pictures" / "b.txt")]


@pytest.mark.parametrize(
    "answer, expected",
    [
        (REPLACE, ["report.txt", "new.txt", "notes.md"]),
        (KEEP_BOTH, ["report (2).txt", "new.txt", "notes (2).md"]),
        (None, None),
    ],
)
def test_names_that_are_taken_are_asked_about(
    qtbot, open_dialog, tree, monkeypatch, answer, expected
):
    questions = []

    def ask(_parent, names, folder, keep_both=False):
        questions.append((names, folder, keep_both))
        return answer

    monkeypatch.setattr(chooser_module, "ask_to_replace", ask)
    asked = Asked(
        portal.SAVE_FILES, folder=gfile(tree), names=["report.txt", "new.txt", "notes.md"]
    )
    dialog = open_dialog(asked)

    dialog.accept_button.click()

    assert questions == [(["report.txt", "notes.md"], "work", True)]
    if expected is None:
        assert not asked.answers and dialog.isVisible()
    else:
        assert asked.uris == [uri(tree / name) for name in expected]


# Around the list


def test_where_it_starts_without_a_folder_from_the_app(qtbot, open_dialog, tree, home_dir):
    first = open_dialog(Asked(portal.OPEN))
    assert first._pane.location.get_path() == str(home_dir.parent)

    settings.set_chooser_folder(uri(tree / "docs"))
    again = open_dialog(Asked(portal.OPEN))
    assert again._pane.location.get_path() == str(tree / "docs")

    # A folder that is gone: the nearest one above it that is there
    settings.set_chooser_folder(uri(tree / "docs" / "gone" / "deeper"))
    after = open_dialog(Asked(portal.OPEN))
    # Found once the folder could not be listed, without a look at it first
    qtbot.waitUntil(lambda: after._pane.location.get_path() == str(tree / "docs"), timeout=TIMEOUT)
    # As if it had been opened there
    assert not after._pane.history.can_go_back


def test_the_sidebar_has_no_trash_and_recent_only_for_opening(qtbot, open_dialog, tree):
    def places(dialog):
        return {entry.name for entry in dialog._sidebar.entries()}

    opening = places(open_dialog(Asked(portal.OPEN, folder=gfile(tree))))
    saving = places(open_dialog(Asked(portal.SAVE, folder=gfile(tree))))

    assert "Home" in opening and "Home" in saving
    assert "Trash" not in opening | saving
    assert "Recent" not in saving


def test_hidden_files_are_this_dialogs_own_choice(qtbot, open_dialog, tree):
    dialog = open_dialog(Asked(portal.OPEN, folder=gfile(tree)))
    assert ".hidden.txt" not in shown(dialog)

    dialog._hidden.trigger()

    assert ".hidden.txt" in shown(dialog)
    assert settings.show_hidden() is False


def test_search_and_the_way_back(qtbot, open_dialog, tree):
    dialog = open_dialog(Asked(portal.OPEN, folder=gfile(tree)))

    dialog._search_field.setText("summary")
    qtbot.waitUntil(lambda: not dialog._pane.searching, timeout=TIMEOUT)
    assert shown(dialog) == ["docs/summary.txt"]

    select(qtbot, dialog, "docs/summary.txt")
    dialog._pane.navigate(gfile(tree / "docs"))
    qtbot.waitUntil(lambda: not dialog._pane.model.loading, timeout=TIMEOUT)
    assert dialog._search_field.text() == ""

    dialog._back.trigger()
    qtbot.waitUntil(lambda: "report.txt" in shown(dialog), timeout=TIMEOUT)
    assert dialog._forward.isEnabled()


def test_what_search_finds_below_can_be_chosen(qtbot, open_dialog, tree):
    asked = Asked(portal.OPEN, folder=gfile(tree))
    dialog = open_dialog(asked)

    dialog._search_field.setText("summary")
    qtbot.waitUntil(lambda: shown(dialog) == ["docs/summary.txt"], timeout=TIMEOUT)
    select(qtbot, dialog, "docs/summary.txt")
    dialog.accept_button.click()

    assert asked.uris == [uri(tree / "docs" / "summary.txt")]


def test_a_new_folder_can_be_made_and_is_selected(qtbot, open_dialog, tree):
    dialog = open_dialog(Asked(portal.SAVE, folder=gfile(tree), name="draft.txt"))

    name_dialog = dialog.new_folder()
    name_dialog.edit.setText("fresh")
    name_dialog.accept()

    qtbot.waitUntil((tree / "fresh").is_dir, timeout=TIMEOUT)
    qtbot.waitUntil(
        lambda: [info.name for info in dialog._view.selected_infos()] == ["fresh"],
        timeout=TIMEOUT,
    )
    # Still the name that was offered: a folder does not name the file
    assert dialog.name.text() == "draft.txt"


def test_a_new_folder_is_offered_a_name_that_is_free(qtbot, open_dialog, tree):
    (tree / "New folder").mkdir()
    dialog = open_dialog(Asked(portal.SAVE, folder=gfile(tree), name="draft.txt"))

    name_dialog = dialog.new_folder()

    qtbot.waitUntil(lambda: name_dialog.edit.text() == "New folder (2)", timeout=TIMEOUT)
    name_dialog.reject()


def test_the_preview_shows_what_is_selected(qtbot, open_dialog, tree):
    dialog = open_dialog(Asked(portal.OPEN, folder=gfile(tree)))

    dialog.toggle_preview()
    assert not dialog._preview.isVisible()

    select(qtbot, dialog, "report.txt")
    dialog.toggle_preview()
    assert dialog._preview.isVisible()

    dialog.reject()
    assert not dialog._preview.isVisible()


def test_its_size_is_remembered_apart_from_the_windows(qtbot, open_dialog, tree):
    dialog = open_dialog(Asked(portal.OPEN, folder=gfile(tree)))
    dialog.resize(777, 555)
    dialog.close()

    again = open_dialog(Asked(portal.OPEN, folder=gfile(tree)))

    assert (again.width(), again.height()) == (777, 555)
