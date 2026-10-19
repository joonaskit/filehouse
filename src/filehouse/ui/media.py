"""Playing a video or an audio file in the preview, with QtMultimedia where the system's
PySide6 has it.

`Media` owns the player and the two widgets that show it: the video, and a row of
controls (play and pause, a slider, the time). It is made the first time a media file is
shown, because starting the multimedia backend is not cheap. Nothing plays by itself: a
preview follows the arrow keys, and a list of songs would otherwise play all of them.
"""

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QToolButton, QWidget

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget
except ImportError:  # Qt without its multimedia modules: a media file has no preview
    QMediaPlayer = None
    QVideoWidget = QWidget

from filehouse.integration import style


def available():
    return QMediaPlayer is not None


def can_play(content_type):
    return available() and content_type.startswith(("video/", "audio/"))


def is_video(content_type):
    return content_type.startswith("video/")


def _clock(milliseconds):
    seconds = max(0, milliseconds) // 1000
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


class _Video(QVideoWidget):
    clicked = Signal()
    double_clicked = Signal()

    def __init__(self):
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.double_clicked.emit()


class Media(QObject):
    # The file can't be played (no codec, a broken file)
    failed = Signal()
    # Its length is known, and whether it has a picture
    ready = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.video = _Video()
        self.player.setVideoOutput(self.video)
        self.video.clicked.connect(self.toggle)

        self.controls = QWidget()
        self._button = QToolButton()
        self._button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._button.clicked.connect(self.toggle)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._slider.sliderMoved.connect(self.player.setPosition)
        self._time = QLabel("0:00 / 0:00")
        self._time.setObjectName("secondary")
        row = QHBoxLayout(self.controls)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(style.px(8))
        row.addWidget(self._button)
        row.addWidget(self._slider, 1)
        row.addWidget(self._time)
        self.retheme()

        self.player.playbackStateChanged.connect(self._on_state)
        self.player.durationChanged.connect(self._on_duration)
        self.player.positionChanged.connect(self._on_position)
        self.player.errorOccurred.connect(self._on_error)
        self.player.hasVideoChanged.connect(lambda _has: self._on_duration())
        self._on_state()

    def retheme(self):
        self._icons = {
            "play": style.icon("media-playback-start-symbolic", "media-playback-start"),
            "pause": style.icon("media-playback-pause-symbolic", "media-playback-pause"),
        }
        self._on_state()

    @property
    def playing(self):
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def load(self, path):
        """Get ready to play the file, paused at its start."""
        self.player.stop()
        self._slider.setValue(0)
        self.player.setSource(QUrl.fromLocalFile(path))
        # A first frame to look at, then paused again
        self.player.pause()

    def stop(self):
        """Stop, and let go of the file."""
        self.player.stop()
        self.player.setSource(QUrl())

    def toggle(self):
        if self.playing:
            self.player.pause()
        else:
            self.player.play()

    def _on_state(self, *_args):
        self._button.setIcon(self._icons["pause" if self.playing else "play"])
        self._button.setToolTip("Pause" if self.playing else "Play")

    def _on_duration(self, *_args):
        duration = self.player.duration()
        self._slider.setRange(0, max(0, duration))
        self._update_time()
        if duration > 0:
            self.ready.emit(self.player.hasVideo())

    def _on_position(self, position):
        if not self._slider.isSliderDown():
            self._slider.setValue(position)
        self._update_time()

    def _update_time(self):
        self._time.setText(
            f"{_clock(self.player.position())} / {_clock(max(0, self.player.duration()))}"
        )

    def _on_error(self, error, _text=""):
        if error != QMediaPlayer.Error.NoError:
            self.failed.emit()
