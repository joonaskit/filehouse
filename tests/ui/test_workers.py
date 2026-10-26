"""Work on worker threads, and its answer to an owner that may be gone by then (#94)."""

import os
import subprocess
import sys
import threading

from PySide6.QtCore import QObject, QThread
from shiboken6 import delete
from ui_helpers import TIMEOUT

from filehouse.qt_models import workers
from filehouse.qt_models.workers import Background

# Owners destroyed while their work answers. Before #94 the worker sent its answer
# through an object the owner deleted, and a deletion that raced the send crashed the
# process in a few hundred rounds. Run apart, so a crash fails this test and not the run
_RACE = """
import random, time
from PySide6.QtCore import QCoreApplication, QObject
from shiboken6 import delete
app = QCoreApplication([])
from filehouse.qt_models.workers import Background
answers = []
for round_ in range(3000):
    owner = QObject()
    background = Background(owner, in_turn=False)
    pause = random.random() * 0.0004
    background.call(lambda: time.sleep(pause), answers.append)
    time.sleep(random.random() * 0.0004)
    delete(owner)
    if round_ % 50 == 0:
        app.processEvents()
time.sleep(0.2)
app.processEvents()
print("ok")
"""


def test_an_owner_destroyed_while_its_work_answers_does_not_crash():
    environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    done = subprocess.run(
        [sys.executable, "-c", _RACE], env=environment, capture_output=True, text=True, timeout=60
    )

    assert done.returncode == 0, done.stderr[-2000:]
    assert done.stdout.strip() == "ok"


def test_an_answer_for_an_owner_that_is_gone_is_dropped(qtbot):
    owner = QObject()
    background = Background(owner, in_turn=False)
    started, go = threading.Event(), threading.Event()
    answers = []

    def work():
        started.set()
        go.wait(5)
        return "answer"

    background.call(work, answers.append)
    assert started.wait(5)
    delete(owner)
    go.set()
    qtbot.waitUntil(lambda: workers.active_count() == 0 and not _running(), timeout=TIMEOUT)
    qtbot.wait(50)

    assert answers == []


def test_the_answer_comes_on_the_owners_thread(qtbot):
    background = Background(in_turn=False)
    threads = []

    def done(result):
        threads.append((QThread.currentThread(), result))

    background.call(lambda: QThread.currentThread(), done)
    qtbot.waitUntil(lambda: bool(threads), timeout=TIMEOUT)

    ((answered_on, worked_on),) = threads
    assert answered_on is background.thread()
    assert worked_on is not background.thread()
    assert not background.busy


def _running():
    return workers._any_order.activeThreadCount() > 0
