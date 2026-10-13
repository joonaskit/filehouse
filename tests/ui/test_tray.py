"""The icon in the tray: what a click and its menu ask for. There is no tray in the
tests, which is the case of a desktop without one."""

from PySide6.QtWidgets import QSystemTrayIcon

from filehouse.ui.tray import Tray


def asked(tray):
    log = []
    tray.open_requested.connect(lambda: log.append("open"))
    tray.new_window_requested.connect(lambda: log.append("new"))
    tray.quit_requested.connect(lambda: log.append("quit"))
    return log


def test_a_click_asks_for_the_window(qtbot):
    tray = Tray()
    log = asked(tray)

    tray._on_activated(QSystemTrayIcon.ActivationReason.Trigger)
    # A right click opens the menu, a middle click does nothing
    tray._on_activated(QSystemTrayIcon.ActivationReason.Context)
    tray._on_activated(QSystemTrayIcon.ActivationReason.MiddleClick)

    assert log == ["open"]


def test_the_menu_opens_a_window_and_ends_the_app(qtbot):
    tray = Tray()
    log = asked(tray)

    assert [a.text() for a in tray._menu.actions() if not a.isSeparator()] == [
        "New window",
        "Quit Filehouse",
    ]
    tray.new_window.trigger()
    tray.quit.trigger()

    assert log == ["new", "quit"]


def test_it_is_shown_and_hidden_as_told_and_works_without_a_tray(qtbot):
    tray = Tray()
    assert not tray.shown

    tray.set_shown(True)
    assert tray.shown
    tray.set_shown(False)

    assert not tray.shown
