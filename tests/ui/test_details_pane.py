import os

import pytest
from ui_helpers import TIMEOUT, gfile

from filehouse.core import properties
from filehouse.core.listing import list_directory
from filehouse.ui.details_pane import DetailsPane


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "notes.txt").write_text("hello")
    (tmp_path / "notes.txt").chmod(0o640)
    (tmp_path / "run.sh").write_text("#!/bin/sh\n")
    (tmp_path / "run.sh").chmod(0o755)
    (tmp_path / "src").mkdir(mode=0o750)
    (tmp_path / "src" / "a.py").write_text("x" * 100)
    (tmp_path / "src" / "deep").mkdir()
    (tmp_path / "src" / "deep" / "b.py").write_text("x" * 200)
    return tmp_path


@pytest.fixture
def pane(qtbot):
    pane = DetailsPane()
    qtbot.addWidget(pane)
    pane.show()
    return pane


def infos(path, *names):
    listed = []
    list_directory(gfile(path), listed.extend)
    by_name = {info.name: info for info in listed}
    return [by_name[name] for name in names]


def show(qtbot, pane, path, *names, item_count=None):
    pane.show_items(gfile(path), infos(path, *names), item_count)
    if len(names) < 2:
        expected = names[0] if names else path.name
        qtbot.waitUntil(lambda: pane.name.toolTip() == expected, timeout=TIMEOUT)


def text(pane, row):
    assert pane.rows[row].isVisible(), row
    return pane.rows[row].text()


def test_a_file_shows_its_details(qtbot, pane, folder):
    show(qtbot, pane, folder, "notes.txt")

    assert pane.name.text().replace("​", "") == "notes.txt"
    assert pane.kind.text() == "Plain text document"
    assert text(pane, "Modified").startswith("Today, ")
    assert text(pane, "Size") == pane._locale.formattedDataSize(5)
    assert text(pane, "Owner").startswith("you")
    assert not pane.rows["Contains"].isVisible()
    assert not pane._icon.pixmap().isNull()
    assert pane.open_button.isVisible()


def test_the_dots_say_what_the_mode_says(qtbot, pane, folder):
    show(qtbot, pane, folder, "notes.txt")
    assert pane.mode.text() == "640"
    assert pane.permissions_shown() == properties.permissions(0o640, False)
    assert [[dot.on for dot in row] for row in pane.dots] == [
        [True, True, False],
        [True, False, False],
        [False, False, False],
    ]
    assert pane.dots[2][0].toolTip() == "Others may not read it"

    show(qtbot, pane, folder, "run.sh")
    assert pane.mode.text() == "755"
    assert pane.permissions_shown() == properties.permissions(0o755, False)


def test_a_folder_is_added_up_and_says_open_for_run(qtbot, pane, folder):
    show(qtbot, pane, folder, "src")

    qtbot.waitUntil(lambda: pane.rows["Contains"].isVisible(), timeout=TIMEOUT)
    qtbot.waitUntil(lambda: not text(pane, "Size").endswith("…"), timeout=TIMEOUT)
    assert text(pane, "Size") == pane._locale.formattedDataSize(300)
    # The folder itself is not counted among what it contains
    assert text(pane, "Contains") == "2 files, 1 folder"
    assert pane.kind.text() == "Folder"
    assert pane.permissions_shown() == properties.permissions(0o750, True)
    assert pane.dots[1][2].toolTip() == "Group may open it"


def test_nothing_selected_shows_the_open_folder_without_walking_it(qtbot, pane, folder):
    show(qtbot, pane, folder, item_count=3)

    assert pane.name.toolTip() == folder.name
    assert pane._row_labels["Size"].text() == "Items"
    assert text(pane, "Size") == "3"
    assert pane._measurement is None
    assert not pane.open_button.isVisible()
    assert pane._permissions.isVisible()


def test_several_items_show_their_count_and_total_size(qtbot, pane, folder):
    show(qtbot, pane, folder, "notes.txt", "run.sh", "src")

    assert pane.name.toolTip() == "3 items"
    assert not pane.open_button.isVisible()
    assert not pane._permissions.isVisible()
    assert not pane.rows["Modified"].isVisible()
    qtbot.waitUntil(lambda: pane.rows["Contains"].isVisible(), timeout=TIMEOUT)
    qtbot.waitUntil(lambda: not text(pane, "Size").endswith("…"), timeout=TIMEOUT)
    assert text(pane, "Size") == pane._locale.formattedDataSize(5 + 10 + 300)
    assert pane._row_labels["Contains"].text() == "In all"
    assert text(pane, "Contains") == "4 files, 2 folders"


def test_a_link_says_where_it_points(qtbot, pane, folder):
    (folder / "link").symlink_to("notes.txt")

    show(qtbot, pane, folder, "link")

    assert pane.kind.text() == "Link to notes.txt"


def test_something_that_is_gone_says_so(qtbot, pane, folder):
    listed = infos(folder, "notes.txt")
    (folder / "notes.txt").unlink()

    pane.show_items(gfile(folder), listed)

    qtbot.waitUntil(lambda: pane.kind.text().startswith("Could not read it"), timeout=TIMEOUT)
    assert pane.name.toolTip() == "notes.txt"
    assert not pane.open_button.isVisible()
    assert not pane._permissions.isVisible()


def test_only_the_last_selection_is_shown(qtbot, pane, folder):
    # Asked for one after the other, faster than they are read
    for name in ("notes.txt", "src", "run.sh"):
        pane.show_items(gfile(folder), infos(folder, name))

    qtbot.waitUntil(lambda: pane.name.toolTip() == "run.sh", timeout=TIMEOUT)
    qtbot.wait(100)
    assert pane.name.toolTip() == "run.sh"
    assert not pane.rows["Contains"].isVisible()
    assert pane._measurement is None


def test_the_same_selection_is_not_read_again_but_a_changed_file_is(qtbot, pane, folder):
    show(qtbot, pane, folder, "notes.txt")
    reading = pane._reading

    pane.show_items(gfile(folder), infos(folder, "notes.txt"))
    assert pane._reading is reading

    (folder / "notes.txt").write_text("hello, world")
    os.utime(folder / "notes.txt", (2_000_000_000, 2_000_000_000))
    pane.show_items(gfile(folder), infos(folder, "notes.txt"))
    assert pane._reading is not reading
    qtbot.waitUntil(
        lambda: text(pane, "Size") == pane._locale.formattedDataSize(12), timeout=TIMEOUT
    )


def test_a_long_name_wraps_and_is_cut_in_the_middle(qtbot, pane, folder):
    name = "a" * 150 + ".txt"
    (folder / name).write_text("")
    pane.resize(250, 600)

    show(qtbot, pane, folder, name)

    assert pane.name.toolTip() == name
    assert "…" in pane.name.text()
    assert pane.width() == 250


def test_open_button_asks_to_open(qtbot, pane, folder):
    show(qtbot, pane, folder, "notes.txt")

    with qtbot.waitSignal(pane.open_requested, timeout=TIMEOUT):
        pane.open_button.click()
