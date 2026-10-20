import hashlib
import os

import pytest
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QImage, QImageReader
from ui_helpers import TIMEOUT, gfile, shared_threads_held

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
    # A text file has nothing in the cache and no thumbnailer: it keeps its type icon
    text = model.index(model.row_for_name("notes.txt"), NAME)
    kept = model.data(text, Qt.ItemDataRole.DecorationRole)
    qtbot.waitUntil(lambda: model._thumbnails["notes.txt"][1] is None, timeout=TIMEOUT)
    qtbot.wait(100)
    assert model.data(text, Qt.ItemDataRole.DecorationRole).cacheKey() == kept.cacheKey()
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


# What other file managers have made


def test_a_thumbnail_in_the_cache_is_used_for_any_type(qtbot, tmp_path):
    document = tmp_path / "report.pdf"
    document.write_text("not really a pdf")
    os.utime(document, (1_600_000_000, 1_600_000_000))
    # As Dolphin leaves it
    made = QImage(96, 128, QImage.Format.Format_RGB32)
    made.fill(QColor("#cc3333"))
    thumbnails._store(made, uri_of(document), 1_600_000_000, 16, QSize())
    model = DirectoryModel()
    model.set_thumbnails(True)
    with qtbot.waitSignal(model.loaded, timeout=TIMEOUT):
        model.set_location(gfile(tmp_path))
    index = model.index(0, NAME)
    plain = model.data(index, Qt.ItemDataRole.DecorationRole)

    qtbot.waitUntil(lambda: model._thumbnails["report.pdf"][1] is not None, timeout=TIMEOUT)

    shown = model.data(index, Qt.ItemDataRole.DecorationRole)
    assert shown.cacheKey() != plain.cacheKey()
    model.close()


def test_a_cached_thumbnail_of_an_older_version_is_not_used(tmp_path):
    document = tmp_path / "report.pdf"
    document.write_text("x")
    thumbnails._store(QImage(8, 8, QImage.Format.Format_RGB32), uri_of(document), 1, 1, QSize())

    assert thumbnails.load(uri_of(document), 2) is None


# The desktop's thumbnailers

FAKE = """\
import sys
from PySide6.QtGui import QColor, QImage
source, output, size = sys.argv[1:4]
if source.endswith("broken.mp4"):
    sys.exit(1)
if source.endswith("slow.mp4"):
    import time
    time.sleep(30)
image = QImage(int(size) * 2, int(size), QImage.Format.Format_RGB32)
image.fill(QColor("#33cc33"))
image.save(output, "PNG")
"""


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    """A data directory with one thumbnailer for videos, and nothing else installed."""
    import sys

    data = tmp_path / "data"
    folder = data / "thumbnailers"
    folder.mkdir(parents=True)
    script = tmp_path / "fake_thumbnailer.py"
    script.write_text(FAKE)
    (folder / "fake.thumbnailer").write_text(
        f"[Thumbnailer Entry]\nExec={sys.executable} {script} %i %o %s\nMimeType=video/mp4;\n"
    )
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "no-user-data"))
    monkeypatch.setenv("XDG_DATA_DIRS", str(data))
    thumbnails.reload_thumbnailers()
    yield data
    thumbnails.reload_thumbnailers()


def video(tmp_path, name="report.mp4"):
    tmp_path.mkdir(exist_ok=True)
    path = tmp_path / name
    path.write_text("x")
    os.utime(path, (1_600_000_000, 1_600_000_000))
    return path


def test_the_thumbnailers_of_the_desktop_are_found_by_type(desktop):
    found = thumbnails.thumbnailer_for("video/mp4")

    assert found.name == "fake.thumbnailer"
    assert thumbnails.thumbnailer_for("audio/flac") is None


def test_a_thumbnailer_whose_program_is_missing_is_ignored(desktop):
    (desktop / "thumbnailers" / "gone.thumbnailer").write_text(
        "[Thumbnailer Entry]\nTryExec=no-such-program-anywhere\n"
        "Exec=no-such-program-anywhere %i %o\nMimeType=audio/flac;\n"
    )
    thumbnails.reload_thumbnailers()

    assert thumbnails.thumbnailer_for("audio/flac") is None


def test_the_users_thumbnailer_comes_before_the_systems(desktop, tmp_path, monkeypatch):
    user = tmp_path / "user"
    (user / "thumbnailers").mkdir(parents=True)
    (user / "thumbnailers" / "mine.thumbnailer").write_text(
        "[Thumbnailer Entry]\nExec=python3 %i %o\nMimeType=video/mp4;\n"
    )
    monkeypatch.setenv("XDG_DATA_HOME", str(user))
    thumbnails.reload_thumbnailers()

    assert thumbnails.thumbnailer_for("video/mp4").name == "mine.thumbnailer"


def test_the_placeholders_are_filled_in():
    command = ["tool", "-i", "%i", "--uri=%u", "-o", "%o", "-s", "%s", "100%%", "%x"]

    assert thumbnails._expand(command, "/a b/c.mp4", "file:///a%20b/c.mp4", "/t/o.png", 128) == [
        "tool",
        "-i",
        "/a b/c.mp4",
        "--uri=file:///a%20b/c.mp4",
        "-o",
        "/t/o.png",
        "-s",
        "128",
        "100%",
        "%x",
    ]


def test_a_thumbnailer_makes_it_and_the_cache_gets_it(desktop, tmp_path):
    path = video(tmp_path)
    uri = uri_of(path)

    image = thumbnails.make_with_thumbnailer(
        thumbnails.thumbnailer_for("video/mp4"), uri, str(path), 1_600_000_000, 1
    )

    # The program drew 256 by 128: kept within the spec's size
    assert (image.width(), image.height()) == (128, 64)
    cached = QImage(thumbnails.cache_path(uri))
    assert cached.text("Thumb::URI") == uri
    assert cached.text("Thumb::MTime") == "1600000000"
    assert thumbnails.load(uri, 1_600_000_000) is not None
    assert not thumbnails.failed(uri, 1_600_000_000)


def test_a_failure_is_remembered_for_that_version_of_the_file(desktop, tmp_path):
    path = video(tmp_path, "broken.mp4")
    uri = uri_of(path)

    image = thumbnails.make_with_thumbnailer(
        thumbnails.thumbnailer_for("video/mp4"), uri, str(path), 1_600_000_000, 1
    )

    assert image is None
    assert not os.path.exists(thumbnails.cache_path(uri))
    assert thumbnails.failed(uri, 1_600_000_000)
    # The spec's place for it
    assert os.path.exists(thumbnails.fail_path(uri))
    assert "/thumbnails/fail/" in thumbnails.fail_path(uri)
    # A changed file is worth another try
    assert not thumbnails.failed(uri, 1_600_000_001)


def test_a_thumbnailer_that_takes_too_long_is_stopped(desktop, tmp_path, monkeypatch):
    monkeypatch.setattr(thumbnails, "_EXTERNAL_SECONDS", 0.5)
    path = video(tmp_path, "slow.mp4")
    uri = uri_of(path)

    image = thumbnails.make_with_thumbnailer(
        thumbnails.thumbnailer_for("video/mp4"), uri, str(path), 1_600_000_000, 1
    )

    assert image is None
    assert thumbnails.failed(uri, 1_600_000_000)


def test_a_huge_file_is_not_handed_to_a_thumbnailer(desktop, tmp_path, monkeypatch):
    monkeypatch.setattr(thumbnails, "_MAX_EXTERNAL_BYTES", 10)
    path = video(tmp_path)
    uri = uri_of(path)

    image = thumbnails.make_with_thumbnailer(
        thumbnails.thumbnailer_for("video/mp4"), uri, str(path), 1_600_000_000, 11
    )

    assert image is None
    # Not a failure of the program: nothing was tried
    assert not thumbnails.failed(uri, 1_600_000_000)


def test_the_thumbnails_of_a_folder_that_was_left_are_not_made(
    qtbot, picture, tmp_path, monkeypatch
):
    looked = []
    monkeypatch.setattr(thumbnails, "load", lambda uri, mtime: looked.append(uri))
    model = DirectoryModel()
    model.set_thumbnails(True)
    with qtbot.waitSignal(model.loaded, timeout=TIMEOUT):
        model.set_location(gfile(picture.parent))

    with shared_threads_held():
        # Asked for, but not started before the folder is left
        model.data(
            model.index(model.row_for_name("photo.png"), NAME), Qt.ItemDataRole.DecorationRole
        )
        other = tmp_path / "other"
        other.mkdir()
        with qtbot.waitSignal(model.loaded, timeout=TIMEOUT):
            model.set_location(gfile(other))

    assert looked == []
    model.close()


def test_the_model_shows_what_a_thumbnailer_made(qtbot, desktop, tmp_path):
    folder = tmp_path / "files"
    video(folder)
    model = DirectoryModel()
    model.set_thumbnails(True)
    with qtbot.waitSignal(model.loaded, timeout=TIMEOUT):
        model.set_location(gfile(folder))
    index = model.index(0, NAME)
    plain = model.data(index, Qt.ItemDataRole.DecorationRole)

    qtbot.waitUntil(lambda: model._thumbnails["report.mp4"][1] is not None, timeout=15000)

    assert model.data(index, Qt.ItemDataRole.DecorationRole).cacheKey() != plain.cacheKey()
    assert os.path.exists(thumbnails.cache_path(uri_of(folder / "report.mp4")))
    model.close()


def test_a_file_a_thumbnailer_failed_on_is_not_tried_again(qtbot, desktop, tmp_path, monkeypatch):
    folder = tmp_path / "files"
    path = video(folder, "broken.mp4")
    model = DirectoryModel()
    model.set_thumbnails(True)
    with qtbot.waitSignal(model.loaded, timeout=TIMEOUT):
        model.set_location(gfile(folder))
    index = model.index(0, NAME)
    model.data(index, Qt.ItemDataRole.DecorationRole)
    qtbot.waitUntil(lambda: thumbnails.failed(uri_of(path), 1_600_000_000), timeout=15000)

    ran = []
    monkeypatch.setattr(thumbnails, "_run", lambda *args: ran.append(args) or False)
    again = DirectoryModel()
    again.set_thumbnails(True)
    with qtbot.waitSignal(again.loaded, timeout=TIMEOUT):
        again.set_location(gfile(folder))
    again.data(again.index(0, NAME), Qt.ItemDataRole.DecorationRole)
    qtbot.wait(300)

    assert ran == []
    model.close()
    again.close()


def test_a_real_pdf_gets_a_thumbnail_from_qt_itself(qtbot, tmp_path):
    from PySide6.QtCore import QMarginsF
    from PySide6.QtGui import QPageSize, QPainter, QPdfWriter

    document = tmp_path / "paper.pdf"
    writer = QPdfWriter(str(document))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A5))
    writer.setPageMargins(QMarginsF(10, 10, 10, 10))
    painter = QPainter(writer)
    painter.fillRect(0, 0, 2000, 800, QColor("#3366cc"))
    painter.end()
    os.utime(document, (1_600_000_000, 1_600_000_000))
    if not thumbnails.can_thumbnail("application/pdf"):
        pytest.skip("this Qt has no PDF image plugin")

    image = thumbnails.make(uri_of(document), str(document), 1_600_000_000, document.stat().st_size)

    assert image is not None and max(image.width(), image.height()) == 128
    assert thumbnails.load(uri_of(document), 1_600_000_000) is not None
