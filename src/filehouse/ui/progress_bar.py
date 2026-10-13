"""The bar of a job: how far it is, or that it is busy."""

from PySide6.QtWidgets import QProgressBar

from filehouse.integration import motion

_STEPS = 1000


class ProgressBar(QProgressBar):
    """Fills smoothly from one report of a job to the next, so it is behind what the
    job has done and never ahead of it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTextVisible(False)
        self._target = None  # the value it is at or on its way to; None while busy
        self._fill = motion.animation(self, b"value", motion.PROGRESS_EASING, self)

    @property
    def target(self):
        return self._target

    def set_fraction(self, fraction):
        """0 to 1, or None for a busy indicator: nothing is known of how much there is."""
        if fraction is None:
            self._fill.stop()
            self._target = None
            self.setRange(0, 0)
            return
        value = round(fraction * _STEPS)
        if value == self._target:
            return
        # A bar that was busy, or is not on show, starts where the job is
        duration = motion.ms(motion.PROGRESS_MS) if self._target is not None else 0
        self._target = value
        self._fill.stop()
        self.setRange(0, _STEPS)
        if not duration or not self.isVisible():
            self.setValue(value)
            return
        self._fill.setDuration(duration)
        self._fill.setStartValue(self.value())
        self._fill.setEndValue(value)
        self._fill.start()
