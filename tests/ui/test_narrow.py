"""The narrow layout: what changes below the width, and what must not."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QToolBar
from ui_helpers import TIMEOUT, gfile

from filehouse.core.locations import Place
from filehouse.integration import style
from filehouse.qt_models.directory_model import MODIFIED, PERMISSIONS
from filehouse.qt_models.volumes import Volumes
from filehouse.ui import main_window
from filehouse.ui.main_window import MainWindow
from filehouse.ui.path_bar import PathBar
from filehouse.ui.sheet import Sheet
from filehouse.ui.sidebar import Sidebar

WIDE = style.px(main_window.NARROW_BELOW) + 200
NARROW = style.px(main_window.NARROW_BELOW) - 200


@pytest.fixture
def folder(tmp_path):
    deep = tmp_path / "Projects" / "island"
    deep.mkdir(parents=True)
    for index in range(60):
        (deep / f"file{index:02}.txt").write_text("x")
    (deep / "run.sh").write_text("")
    (deep / "run.sh").chmod(0o750)
    return deep


@pytest.fixture
def window(qtbot, folder):
    window = MainWindow(gfile(folder))
    qtbot.addWidget(window)
    window.resize(WIDE, 600)
    window.show()
    qtbot.waitUntil(lambda: not window._model.loading, timeout=TIMEOUT)
    return window


def button(window, action):
    return window._button_for(action)


def test_a_wide_window_has_the_wide_layout(window):
    assert not window.is_narrow
    assert window._details.isVisible()
    assert not window._sidebar.is_rail
    assert not window._action_bar.isVisible()
    assert button(window, window._forward).isVisible()
    assert not button(window, window._folder_menu).isVisible()


def test_below_the_width_the_layout_changes(qtbot, window, folder):
    window._view.select_name("run.sh")

    window.resize(NARROW, 600)

    assert window.is_narrow
    # The sidebar is a rail of icons, the details pane is gone
    assert window._sidebar.is_rail
    assert window._sidebar.width() < style.px(80)
    assert not window._details.isVisible()
    # The breadcrumb names the folder and the one it is in
    assert window._path_bar.crumbs() == ["…", "Projects", "island"]
    # Back and up stay; forward and the actions make way, the folder menu gets a button
    assert button(window, window._back).isVisible()
    assert not button(window, window._forward).isVisible()
    toolbar = window.findChild(QToolBar)
    in_toolbar = [b for b in toolbar.findChildren(type(button(window, window._copy)))]
    copy_buttons = [b for b in in_toolbar if b.defaultAction() is window._copy]
    assert copy_buttons
    # The toolbar puts its widgets away when it next lays itself out
    qtbot.waitUntil(lambda: not any(b.isVisible() for b in copy_buttons), timeout=TIMEOUT)
    assert button(window, window._folder_menu).isVisible()
    # The actions are in the bar at the bottom, for what is selected
    assert window._action_bar.isVisible()
    # The list gives up the Modified column
    tree = window._view._tree
    assert tree.isColumnHidden(MODIFIED) and not tree.isColumnHidden(PERMISSIONS)
    # What the list has no room for is in the status bar
    assert window._count_label.text() == "run.sh · rwxr-x---"


def test_the_window_does_not_get_narrower_than_its_toolbar(qtbot, window):
    from PySide6.QtWidgets import QFrame

    window.resize(100, 100)

    assert window.width() >= style.px(460) and window.height() >= style.px(360)
    toolbar = window.findChild(QToolBar)
    qtbot.waitUntil(lambda: button(window, window._folder_menu).isVisible(), timeout=TIMEOUT)
    # Every group that is on show is wholly inside the window
    groups = [
        frame
        for frame in toolbar.findChildren(QFrame)
        if frame.objectName() == "group" and frame.isVisible()
    ]
    assert len(groups) == 4
    for group in groups:
        right = group.mapTo(window, group.rect().topRight()).x()
        assert right <= window.width()


def test_going_back_to_wide_restores_everything(qtbot, window):
    before = window._splitter.sizes()
    window.resize(NARROW, 600)
    window._view.select_name("run.sh")
    window._show_details.setChecked(True)

    window.resize(WIDE, 600)

    assert not window.is_narrow
    assert window._details.isVisible() and not window._sidebar.is_rail
    assert not window._action_bar.isVisible() and not window._sheet.is_open
    assert not window._show_details.isChecked()
    assert button(window, window._forward).isVisible()
    assert not window._view._tree.isColumnHidden(MODIFIED)
    assert window._path_bar.crumbs()[0] != "…"
    assert window._splitter.sizes() == before
    assert window._count_label.text() == "61 items · run.sh selected"
    qtbot.waitUntil(lambda: window._details.name.toolTip() == "run.sh", timeout=TIMEOUT)


def test_switching_keeps_the_selection_and_the_place_in_the_list(qtbot, window):
    window._view.select_name("file40.txt")
    qtbot.waitUntil(lambda: window._view.scroll_position() > 0, timeout=TIMEOUT)
    place = window._view.scroll_position()

    for width in (NARROW, WIDE, NARROW):
        window.resize(width, 600)
        assert [info.name for info in window._view.selected_infos()] == ["file40.txt"]
        assert window._view.scroll_position() == place


def test_the_action_bar_is_there_only_with_a_selection(qtbot, window):
    window.resize(NARROW, 600)
    assert not window._action_bar.isVisible()

    window._view.select_name("run.sh")
    assert window._action_bar.isVisible()

    window._view.selection_model().clear()
    assert not window._action_bar.isVisible()


def test_the_sheet_shows_the_details_and_follows_the_selection(qtbot, window):
    window.resize(NARROW, 600)
    window._view.select_name("run.sh")
    sheet, pane = window._sheet, window._sheet_details

    button(window, window._show_details).click()

    assert sheet.is_open and sheet.isVisible()
    # The bar makes way for it
    assert not window._action_bar.isVisible()
    qtbot.waitUntil(lambda: pane.name.toolTip() == "run.sh", timeout=TIMEOUT)
    assert pane.mode.text() == "rwxr-x--- · 750"
    assert not pane.open_button.isVisible()

    window._view.select_name("file03.txt")
    qtbot.waitUntil(lambda: pane.name.toolTip() == "file03.txt", timeout=TIMEOUT)

    pane.close_button.click()
    assert not sheet.is_open and not window._show_details.isChecked()
    qtbot.waitUntil(lambda: not sheet.isVisible(), timeout=TIMEOUT)
    assert window._action_bar.isVisible()


def test_escape_closes_the_sheet_and_does_nothing_else_without_one(qtbot, window):
    window.resize(NARROW, 600)
    window._view.select_name("run.sh")
    assert not window._close_details.isEnabled()

    window._show_details.setChecked(True)
    assert window._close_details.isEnabled()
    window._close_details.trigger()

    assert not window._sheet.is_open and not window._close_details.isEnabled()


def test_what_is_selected_stays_in_view_when_the_sheet_takes_the_room(qtbot, window):
    window.resize(NARROW, 600)
    window._view.select_name("file05.txt")
    view = window._view

    def in_view():
        tree = view._tree
        return tree.viewport().rect().contains(tree.visualRect(tree.currentIndex()))

    assert in_view()
    window._show_details.setChecked(True)

    qtbot.waitUntil(lambda: window._sheet.height() > 100, timeout=TIMEOUT)
    qtbot.waitUntil(in_view, timeout=TIMEOUT)


def test_the_menu_buttons_show_the_folder_menu_and_the_selection_menu(qtbot, window, monkeypatch):
    shown = []
    monkeypatch.setattr(
        window, "_on_context_menu", lambda info, position, above=False: shown.append((info, above))
    )
    window.resize(NARROW, 600)
    window._view.select_name("run.sh")

    button(window, window._folder_menu).click()
    button(window, window._more).click()

    assert shown[0] == (None, False)
    # From the bar at the bottom, the menu opens upwards
    assert shown[1][0].name == "run.sh" and shown[1][1] is True


def test_menus_are_made_without_being_shown(qtbot, window):
    folder_menu = window._menu_for(None)
    texts = [action.text() for action in folder_menu.actions() if action.text()]
    assert texts[:4] == ["New folder…", "New file…", "Paste", "Select all"]

    window._view.select_name("run.sh")
    file_menu = window._menu_for(window._view.selected_infos()[0])
    assert "Rename…" in [action.text() for action in file_menu.actions()]


def test_the_job_line_is_one_word_in_a_narrow_window(qtbot, window):
    from test_job_status import job

    window.resize(NARROW, 600)
    one = job(count=4, done=62, total=100)
    one.id, one.errors, one.error, one.can_retry = 9001, [], None, False
    one.cancellable = type("C", (), {"is_cancelled": lambda self: False})()
    window._jobs._jobs.append(one)
    window._jobs.job_added.emit(one)

    assert window._job_status.title.text() == "Copying"
    assert window._job_status.percent.text() == "62 %"

    window.resize(WIDE, 600)
    assert window._job_status.title.text() == "Copying 4 items"
    window._jobs._jobs.remove(one)
    window._jobs.job_removed.emit(one)


def test_the_lock_in_the_action_bar_opens_the_presets_upwards(qtbot, window, monkeypatch):
    from filehouse.ui import menus

    shown = []
    make = menus.menu

    def menu_that_does_not_block(parent, title=None):
        # Patched on the menu itself: Qt calls its own exec past a patched class
        made = make(parent, title)
        made.exec = lambda position: shown.append((made.title(), position.y()))
        return made

    monkeypatch.setattr(menus, "menu", menu_that_does_not_block)
    window.resize(NARROW, 600)
    window._view.select_name("run.sh")
    lock = button(window, window._permissions)
    assert lock.isVisible() and lock.parent() is window._action_bar

    lock.click()

    assert len(shown) == 1 and shown[0][0] == "Permissions"
    assert shown[0][1] < lock.mapToGlobal(lock.rect().topLeft()).y()


# The parts by themselves


def segments(*names):
    return [(name, gfile("/" + "/".join(names[: index + 1]))) for index, name in enumerate(names)]


def test_short_breadcrumbs_keep_the_rest_in_reach(qtbot):
    bar = PathBar()
    qtbot.addWidget(bar)
    chosen = []
    bar.location_chosen.connect(lambda location: chosen.append(location.get_path()))
    bar.set_location(segments("a", "b", "c", "d"), "/a/b/c/d")
    assert bar.crumbs() == ["a", "b", "c", "d"]

    bar.set_short(True)
    assert bar.crumbs() == ["…", "c", "d"]

    # Two or fewer need no "…"
    bar.set_location(segments("a", "b"), "/a/b")
    assert bar.crumbs() == ["a", "b"]

    bar.set_short(False)
    bar.set_location(segments("a", "b", "c"), "/a/b/c")
    assert bar.crumbs() == ["a", "b", "c"]


def test_the_rail_shows_icons_with_names_as_tooltips(qtbot, tmp_path):
    from test_volumes import FakeMonitor, FakeMount

    places = [Place("Home", "user-home", gfile(tmp_path)), Place("Docs", "folder", gfile("/docs"))]
    monitor = FakeMonitor([FakeMount("USB stick", tmp_path / "stick")], [])
    sidebar = Sidebar(places, Volumes(monitor=monitor))
    qtbot.addWidget(sidebar)
    sidebar.select(gfile("/docs"))

    sidebar.set_rail(True)

    texts = [sidebar.item(row).text() for row in range(sidebar.count())]
    assert texts == ["", "", "", ""]  # two places, a divider, a device: no headings
    assert [entry.name for entry in sidebar.entries()] == ["Home", "Docs", "USB stick"]
    assert sidebar.item(sidebar.row_of("Docs")).toolTip() == "Docs"
    assert sidebar.currentRow() == sidebar.row_of("Docs")
    # The divider is not a place
    assert not sidebar.item(2).flags() & Qt.ItemFlag.ItemIsSelectable

    went = []
    sidebar.place_activated.connect(lambda location: went.append(location.get_path()))
    rect = sidebar.visualItemRect(sidebar.item(sidebar.row_of("Home")))
    qtbot.mouseClick(sidebar.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert went == [str(tmp_path)]

    sidebar.set_rail(False)
    assert [sidebar.item(row).text() for row in range(sidebar.count())] == [
        "Places",
        "Home",
        "Docs",
        "Devices",
        "USB stick",
    ]
    assert sidebar.maximumWidth() > 1000


def test_a_sheet_slides_open_and_shut(qtbot):
    from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

    holder = QWidget()
    qtbot.addWidget(holder)
    layout = QVBoxLayout(holder)
    layout.addWidget(QLabel("list"), 1)
    content = QLabel("details")
    content.setFixedHeight(120)
    sheet = Sheet(content)
    layout.addWidget(sheet)
    holder.resize(300, 400)
    holder.show()
    assert not sheet.isVisible()

    with qtbot.waitSignal(sheet.settled, timeout=TIMEOUT):
        sheet.set_open(True)
    assert sheet.isVisible() and sheet.height() >= 120

    with qtbot.waitSignal(sheet.settled, timeout=TIMEOUT):
        sheet.set_open(False)
    assert not sheet.isVisible()

    # Without the slide, for a window that is not on show
    sheet.set_open(True, animate=False)
    assert sheet.isVisible() and sheet.maximumHeight() > 1000


@pytest.mark.parametrize("scale", [1.0, 1.1, 1.5])
def test_rail_icons_are_in_the_middle_of_the_rail(qtbot, qapp, tmp_path, scale):
    # Painted by the style, an icon without a text sat off to the right
    from test_volumes import FakeMonitor

    from filehouse.integration import theme

    try:
        style.apply(
            qapp,
            theme.merge(
                theme.DEFAULTS,
                {"radius": {"small": 9, "medium": 15, "large": 21}, "scale": {"ui": scale}},
            ),
        )
        places = [Place("Home", "no-such-icon", gfile(tmp_path)), Place("Docs", "x", gfile("/d"))]
        sidebar = Sidebar(places, Volumes(monitor=FakeMonitor()))
        qtbot.addWidget(sidebar)
        sidebar.set_rail(True)
        sidebar.resize(sidebar.maximumWidth(), 300)
        sidebar.show()

        image = sidebar.grab().toImage()
        row = sidebar.visualItemRect(sidebar.item(0))
        y = sidebar.viewport().mapTo(sidebar, row.center()).y()
        card = image.pixelColor(image.width() // 2, image.height() - 20)
        # The tile is what differs from the card along the middle of the row
        tile = [
            x
            for x in range(2, image.width() - 2)
            if abs(image.pixelColor(x, y).lightness() - card.lightness()) > 3
            or abs(image.pixelColor(x, y).blue() - card.blue()) > 6
        ]
        assert tile
        left, right = tile[0], image.width() - 1 - tile[-1]
        assert abs(left - right) <= 1, (left, right)
        assert tile[-1] - tile[0] + 1 >= style.px(26)
    finally:
        style.apply(qapp, theme.merge(theme.DEFAULTS, {}))
        qapp.setStyleSheet("")
