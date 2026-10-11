import hashlib
import os

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QImageReader
from ui_helpers import TIMEOUT, gfile

from filehouse.qt_models import thumbnails
from filehouse.qt_models.directory_model import NAME, DirectoryModel


@pytest.fixture
def picture(tmp_path):
    path = tmp_path / "photo.png"
    image = QImage(640, 320, QImage.Format.Format_RGB32)
    image.fill(QColor("#3366cc"))
    assert image.save(str(path))
    os.utime(path, (1_600_000_000, 1_600_000_000))
    return path


def uri_of(path):
    return gfile(path).get_uri()


def test_cache_follows_the_freedesktop_layout(picture):
    uri = uri_of(picture)

    cached = thumbnails.cache_path(uri)

    assert cached.startswith(os.path.join(os.environ["XDG_CACHE_HOME"], "thumbnails", "normal"))
    assert os.path.basename(cached) == hashlib.md5(uri.encode()).hexdigest() + ".png"


def test_makes_a_thumbnail_and_writes_it_as_the_spec_asks(picture):
    uri = uri_of(picture)

    image = thumbnails.make(uri, str(picture), 1_600_000_000, picture.stat().st_size)

    assert (image.width(), image.height()) == (128, 64)
    cached = thumbnails.cache_path(uri)
    assert QImageReader(cached).format() == b"png"
    written = QImage(cached)
    assert written.text("Thumb::URI") == uri
    assert written.text("Thumb::MTime") == "1600000000"
    assert written.text("Thumb::Image::Width") == "640"
    assert os.stat(cached).st_mode & 0o777 == 0o600
    assert os.stat(os.path.dirname(cached)).st_mode & 0o777 == 0o700
    # Nothing half-written is left next to it
    assert os.listdir(os.path.dirname(cached)) == [os.path.basename(cached)]


def test_a_cached_thumbnail_is_used_until_the_file_changes(picture):
    uri = uri_of(picture)
    assert thumbnails.load(uri, 1_600_000_000) is None
    thumbnails.make(uri, str(picture), 1_600_000_000, picture.stat().st_size)

    assert thumbnails.load(uri, 1_600_000_000).width() == 128
    assert thumbnails.load(uri, 1_600_000_001) is None


def test_small_images_are_not_enlarged(tmp_path):
    path = tmp_path / "icon.png"
    image = QImage(32, 16, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.red)
    image.save(str(path))

    made = thumbnails.make(uri_of(path), str(path), 1, path.stat().st_size)

    assert (made.width(), made.height()) == (32, 16)


def test_what_is_not_an_image_gives_nothing(tmp_path):
    path = tmp_path / "broken.png"
    path.write_text("not a picture")

    assert thumbnails.make(uri_of(path), str(path), 1, path.stat().st_size) is None
    assert not os.path.exists(thumbnails.cache_path(uri_of(path)))
    assert thumbnails.can_thumbnail("image/png") and thumbnails.can_thumbnail("image/jpeg")
    assert not thumbnails.can_thumbnail("text/plain")
    assert not thumbnails.can_thumbnail("inode/directory")


def test_huge_files_and_the_cache_itself_are_left_alone(picture, monkeypatch):
    monkeypatch.setattr(thumbnails, "_MAX_BYTES", 10)
    assert thumbnails.make(uri_of(picture), str(picture), 1, picture.stat().st_size) is None

    monkeypatch.setattr(thumbnails, "_MAX_BYTES", 10**9)
    monkeypatch.setattr(thumbnails, "cache_dir", lambda: str(picture.parent))
    assert thumbnails.make(uri_of(picture), str(picture), 1, 100) is None


def test_the_model_shows_thumbnails_only_when_asked(qtbot, picture):
    (picture.parent / "notes.txt").write_text("x")
    model = DirectoryModel()
    with qtbot.waitSignal(model.loaded, timeout=TIMEOUT):
        model.set_location(gfile(picture.parent))
    index = model.index(model.row_for_name("photo.png"), NAME)
    plain = model.data(index, Qt.ItemDataRole.DecorationRole)
    qtbot.wait(100)
    # Details mode: the type icon, and nothing was made
    assert not os.path.exists(thumbnails.cache_path(uri_of(picture)))

    model.set_thumbnails(True)
    with qtbot.waitSignal(model.dataChanged, timeout=TIMEOUT) as changed:
        model.data(index, Qt.ItemDataRole.DecorationRole)

    assert changed.args[0].row() == index.row()
    thumbnail = model.data(index, Qt.ItemDataRole.DecorationRole)
    assert thumbnail.cacheKey() != plain.cacheKey()
    size = thumbnail.availableSizes()[0]
    assert (size.width(), size.height()) == (128, 128)
    assert os.path.exists(thumbnails.cache_path(uri_of(picture)))
    # A text file keeps its type icon and is never asked about
    text = model.index(model.row_for_name("notes.txt"), NAME)
    model.data(text, Qt.ItemDataRole.DecorationRole)
    assert "notes.txt" not in model._thumbnails
    model.close()


def test_a_changed_picture_gets_a_new_thumbnail(qtbot, picture):
    model = DirectoryModel()
    model.set_thumbnails(True)
    with qtbot.waitSignal(model.loaded, timeout=TIMEOUT):
        model.set_location(gfile(picture.parent))
    index = model.index(0, NAME)
    with qtbot.waitSignal(model.dataChanged, timeout=TIMEOUT):
        model.data(index, Qt.ItemDataRole.DecorationRole)
    first = model.data(index, Qt.ItemDataRole.DecorationRole).cacheKey()

    image = QImage(100, 400, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.green)
    image.save(str(picture))
    os.utime(picture, (1_700_000_000, 1_700_000_000))
    qtbot.waitUntil(lambda: model.info(0).modified == 1_700_000_000, timeout=TIMEOUT)
    qtbot.waitUntil(
        lambda: (
            model.data(index, Qt.ItemDataRole.DecorationRole).cacheKey() != first
            and model._thumbnails["photo.png"][1] is not None
        ),
        timeout=TIMEOUT,
    )

    assert model.data(index, Qt.ItemDataRole.DecorationRole).availableSizes()[0].height() == 128
    model.close()
