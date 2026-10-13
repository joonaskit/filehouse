"""What moves: the panels that slide and the bars that fill. Everywhere else in the
tests the desktop's animations are off (see tests/conftest.py)."""

import pytest
from PySide6.QtWidgets import QLabel, QSplitter, QVBoxLayout, QWidget
from ui_helpers import TIMEOUT

from filehouse.integration import motion
from filehouse.ui.progress_bar import ProgressBar
from filehouse.ui.slide import SideSlide, Slide


@pytest.fixture
def moving(monkeypatch):
    monkeypatch.setattr(motion, "factor", lambda: 1.0)


@pytest.fixture
def column(qtbot):
    """A list with a panel below it, in a window on show."""
    holder = QWidget()
    qtbot.addWidget(holder)
    layout = QVBoxLayout(holder)
    layout.addWidget(QLabel("list"), 1)
    panel = QLabel("panel")
    panel.setFixedHeight(120)
    layout.addWidget(panel)
    slide = Slide(panel)
    holder.resize(300, 400)
    holder.show()
    yield panel, slide
    holder.close()


def test_a_panel_slides_open_and_shut(column, moving, qtbot):
    panel, slide = column
    assert not panel.isVisible()

    slide.set_shown(True)
    # On its way: there to see, and not yet at its height
    assert slide.shown and panel.isVisible() and panel.maximumHeight() == 0
    with qtbot.waitSignal(slide.settled, timeout=TIMEOUT):
        pass
    assert panel.height() == 120 and panel.maximumHeight() > 1000

    slide.set_shown(False)
    # Still there to see while it closes
    assert not slide.shown and panel.isVisible()
    with qtbot.waitSignal(slide.settled, timeout=TIMEOUT):
        pass
    assert not panel.isVisible()


def test_a_panel_turned_back_half_way_ends_where_it_was_last_sent(column, moving, qtbot):
    panel, slide = column
    slide.set_shown(True)
    slide.set_shown(False)
    slide.set_shown(True)

    qtbot.waitUntil(lambda: panel.height() == 120, timeout=TIMEOUT)
    assert panel.isVisible()


def test_nothing_slides_with_the_desktops_animations_off(column):
    panel, slide = column

    slide.set_shown(True)
    assert panel.isVisible() and panel.maximumHeight() > 1000

    slide.set_shown(False)
    assert not panel.isVisible()


def test_nothing_slides_in_a_window_that_is_not_on_show(moving, qtbot):
    panel = QLabel("panel")
    qtbot.addWidget(panel)
    slide = Slide(panel)

    slide.set_shown(True)

    assert not panel.isHidden() and panel.maximumHeight() > 1000


@pytest.fixture
def row(qtbot):
    """A list with a pane beside it, in a window on show."""
    splitter = QSplitter()
    qtbot.addWidget(splitter)
    splitter.addWidget(QLabel("list"))
    pane = QLabel("pane")
    pane.setMinimumWidth(100)
    splitter.addWidget(pane)
    splitter.resize(600, 300)
    splitter.show()
    splitter.setSizes([400, 200])
    yield splitter, pane, SideSlide(splitter, pane)
    splitter.close()


def test_a_pane_slides_away_and_comes_back_as_wide_as_it_was(row, moving, qtbot):
    splitter, pane, slide = row
    width = pane.width()
    whole = sum(splitter.sizes())

    slide.set_shown(False)
    assert not slide.shown and pane.isVisible()
    qtbot.waitUntil(lambda: not pane.isVisible(), timeout=TIMEOUT)
    # Its room is the list's
    full = whole + splitter.handleWidth()
    qtbot.waitUntil(lambda: splitter.sizes()[0] == full, timeout=TIMEOUT)
    assert pane.minimumWidth() == 100

    slide.set_shown(True)
    assert pane.isVisible() and pane.width() < width
    qtbot.waitUntil(lambda: pane.width() == width, timeout=TIMEOUT)
    assert pane.minimumWidth() == 100


def test_a_pane_turned_back_half_way_is_as_wide_as_it_was(row, moving, qtbot):
    splitter, pane, slide = row
    width = pane.width()

    slide.set_shown(False)
    qtbot.waitUntil(lambda: pane.width() < width, timeout=TIMEOUT)
    slide.set_shown(True)

    qtbot.waitUntil(lambda: pane.width() == width, timeout=TIMEOUT)
    qtbot.waitUntil(lambda: pane.minimumWidth() == 100, timeout=TIMEOUT)


def test_a_pane_is_hidden_at_once_with_the_desktops_animations_off(row):
    _splitter, pane, slide = row
    width = pane.width()

    slide.set_shown(False)
    assert not pane.isVisible()

    slide.set_shown(True)
    assert pane.isVisible() and pane.width() == width


@pytest.fixture
def bar(qtbot):
    bar = ProgressBar()
    qtbot.addWidget(bar)
    bar.show()
    return bar


def test_a_bar_starts_where_the_job_is(bar, moving):
    bar.set_fraction(None)
    assert bar.maximum() == 0

    bar.set_fraction(0.4)

    assert bar.maximum() == 1000 and bar.value() == 400


def test_a_bar_fills_to_the_next_report_and_is_never_ahead_of_it(bar, moving, qtbot):
    bar.set_fraction(0.2)
    seen = []
    bar.valueChanged.connect(seen.append)

    bar.set_fraction(0.6)

    assert bar.target == 600 and bar.value() < 600
    qtbot.waitUntil(lambda: bar.value() == 600, timeout=TIMEOUT)
    assert len(seen) > 1 and max(seen) == 600 and seen == sorted(seen)


def test_a_bar_jumps_with_the_desktops_animations_off(bar):
    bar.set_fraction(0.2)

    bar.set_fraction(0.6)

    assert bar.value() == 600


def test_a_bar_that_turns_busy_stops_filling(bar, moving, qtbot):
    bar.set_fraction(0.2)
    bar.set_fraction(0.6)

    bar.set_fraction(None)

    assert bar.maximum() == 0 and bar.target is None
