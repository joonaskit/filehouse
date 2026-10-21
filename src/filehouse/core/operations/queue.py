"""Runs jobs a few at a time; the rest wait as `queued`. A job that waits for an answer
from the user does not count: it holds up nothing else meanwhile."""

import threading
from collections import deque

from filehouse.core.operations.job import JobState

DEFAULT_LIMIT = 2


class JobQueue:
    """Starts jobs in the order they were added, at most `limit` at once.

    A job that asks the user something (state `waiting`) gives its place back while
    it waits, and the next job starts. Once it is answered it goes on at once, even
    when that makes one more than `limit` for a while: it was the user's turn, not
    the job's, and an answered job that then shows "Waiting to start" would be odd.

    The core starts no threads of its own: `spawn(function)` is supplied by the
    caller and must run `function` in the background.
    """

    def __init__(self, spawn, limit=DEFAULT_LIMIT):
        self._spawn = spawn
        self._limit = limit
        self._lock = threading.Lock()
        self._waiting = deque()
        self._running = set()
        self._asking = set()  # started, and waiting for an answer

    def add(self, job):
        job.on_wait = lambda asking, job=job: self._on_wait(job, asking)
        with self._lock:
            self._waiting.append(job)
        self._fill()

    def is_idle(self):
        with self._lock:
            return not self._waiting and not self._running and not self._asking

    def _on_wait(self, job, asking):
        """Called on the job's thread when it starts or stops waiting for an answer."""
        with self._lock:
            if asking:
                self._running.discard(job)
                self._asking.add(job)
            else:
                self._asking.discard(job)
                self._running.add(job)
        if asking:
            self._fill()

    def _fill(self):
        starting = []
        with self._lock:
            while self._waiting and len(self._running) < self._limit:
                job = self._waiting.popleft()
                # A job cancelled while it waited has already reported that
                if job.state is JobState.QUEUED:
                    self._running.add(job)
                    starting.append(job)
        for job in starting:
            self._spawn(lambda job=job: self._work(job))

    def _work(self, job):
        try:
            job.run()
        finally:
            with self._lock:
                self._running.discard(job)
                self._asking.discard(job)
            self._fill()
