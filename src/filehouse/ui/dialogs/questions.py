"""The questions a job can ask, as message boxes, and the answer each button gives."""

from PySide6.QtWidgets import QCheckBox, QMessageBox

from filehouse.core import locations
from filehouse.core.operations import (
    Answer,
    ConfirmDelete,
    Conflict,
    Failure,
    Reply,
    TrashUnsupported,
)

_ROLES = QMessageBox.ButtonRole


class QuestionBox(QMessageBox):
    """A message box for one question. `reply()` is the answer once it has closed."""

    def __init__(self, question, parent=None):
        super().__init__(parent)
        self.question = question
        self._answers = {}
        self._all = None
        self.setIcon(QMessageBox.Icon.Question)
        if isinstance(question, Conflict):
            self._conflict(question)
        elif isinstance(question, Failure):
            self._failure(question)
        elif isinstance(question, TrashUnsupported):
            self._no_trash(question)
        elif isinstance(question, ConfirmDelete):
            self._confirm_delete(question)
        else:
            raise TypeError(f"no dialog for {type(question).__name__}")

    def reply(self):
        """The Reply for the button that was clicked. Closing the box cancels the job."""
        answer = self._answers.get(self.clickedButton(), Answer.CANCEL)
        return Reply(answer, self._all is not None and self._all.isChecked())

    def button_for(self, answer):
        return next(button for button, value in self._answers.items() if value is answer)

    def _add(self, text, answer, role=_ROLES.ActionRole):
        self._answers[self.addButton(text, role)] = answer

    def _add_cancel(self, text="Cancel"):
        button = self.addButton(text, _ROLES.RejectRole)
        self._answers[button] = Answer.CANCEL
        self.setEscapeButton(button)

    def _offer_all(self, text):
        self._all = QCheckBox(text)
        self.setCheckBox(self._all)

    def _conflict(self, question):
        name = locations.basename(question.destination)
        folder = locations.display_name(locations.parent(question.destination))
        both_folders = question.source_is_dir and question.destination_is_dir
        self.setWindowTitle("Folder exists" if question.destination_is_dir else "File exists")
        self.setText(f"“{name}” already exists in {folder}.")
        if locations.same(question.source, question.destination):
            self.setInformativeText("It is the item being copied. You can keep both.")
        elif not question.can_replace:
            kinds = ("folder", "file") if question.destination_is_dir else ("file", "folder")
            self.setInformativeText(f"A {kinds[0]} cannot be replaced with a {kinds[1]}.")
        elif both_folders:
            self.setInformativeText("Merging keeps what is in both folders.")
        if question.can_replace:
            self._add("Merge" if both_folders else "Replace", Answer.REPLACE)
        self._add("Keep both", Answer.KEEP_BOTH)
        self._add("Skip", Answer.SKIP)
        self._add_cancel()
        self._offer_all("Do this for all conflicts")

    def _failure(self, question):
        error = question.error
        self.setIcon(QMessageBox.Icon.Warning)
        self.setWindowTitle("Something went wrong")
        self.setText(f"Could not {error.step} “{error.name}”: {error.message}.")
        if error.path:
            self.setInformativeText(error.path)
        self._add("Retry", Answer.RETRY)
        self._add("Skip", Answer.SKIP)
        self._add_cancel()
        self._offer_all("Skip all that fail")

    def _no_trash(self, question):
        name = locations.basename(question.location)
        self.setIcon(QMessageBox.Icon.Warning)
        self.setWindowTitle("No trash here")
        self.setText(f"“{name}” cannot be moved to the trash.")
        self.setInformativeText("Delete it permanently instead? This cannot be undone.")
        self._add("Delete permanently", Answer.DELETE, _ROLES.DestructiveRole)
        self._add("Skip", Answer.SKIP)
        self._add_cancel()
        self._offer_all("Do this for all items")

    def _confirm_delete(self, question):
        count = len(question.sources)
        if count == 1:
            what = f"“{locations.basename(question.sources[0])}”"
        else:
            what = f"{count} items"
        self.setIcon(QMessageBox.Icon.Warning)
        self.setWindowTitle("Delete permanently")
        self.setText(f"Permanently delete {what}?")
        self.setInformativeText("This cannot be undone.")
        self._add("Delete", Answer.DELETE, _ROLES.DestructiveRole)
        self._add_cancel()
