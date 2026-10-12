from gi.repository import Gio, GLib

from filehouse.core import locations
from filehouse.core.history import History


def path_of(location):
    return location.get_path()


def test_display_names():
    assert locations.display_name(locations.home()) == "Home"
    assert locations.display_name(Gio.File.new_for_path("/")) == "/"
    assert locations.display_name(Gio.File.new_for_path("/usr/share")) == "share"


def test_segments_run_from_root_to_the_location():
    segments = locations.segments(Gio.File.new_for_path("/usr/share/icons"))

    assert [name for name, _ in segments] == ["/", "usr", "share", "icons"]
    assert [path_of(location) for _, location in segments] == [
        "/",
        "/usr",
        "/usr/share",
        "/usr/share/icons",
    ]


def test_user_input_understands_tilde_paths_and_uris():
    home = GLib.get_home_dir()

    assert path_of(locations.from_user_input("~")) == home
    assert path_of(locations.from_user_input(" ~/Music ")) == home + "/Music"
    assert path_of(locations.from_user_input("/tmp")) == "/tmp"
    assert path_of(locations.from_user_input("file:///tmp")) == "/tmp"


def test_start_at_a_folder(tmp_path):
    target = locations.resolve_start(str(tmp_path))

    assert path_of(target.location) == str(tmp_path)
    assert target.select is None and target.message is None


def test_start_at_a_file_opens_its_folder_with_the_file_selected(tmp_path):
    (tmp_path / "report.pdf").write_text("")

    target = locations.resolve_start((tmp_path / "report.pdf").as_uri())

    assert path_of(target.location) == str(tmp_path)
    assert target.select == "report.pdf"


def test_start_at_a_missing_path_opens_home_with_a_message(tmp_path):
    target = locations.resolve_start(str(tmp_path / "missing"))

    assert locations.same(target.location, locations.home())
    assert "missing" in target.message


def test_places_start_with_home_and_exist():
    places = locations.places()

    assert places[0].name == "Home"
    assert all(place.location.query_exists(None) for place in places)


def test_history_back_and_forward():
    history = History()
    for place in ("a", "b", "c"):
        history.visit(place)

    assert history.back() == "b"
    assert history.back() == "a"
    assert not history.can_go_back
    assert history.forward() == "b"

    history.visit("d")

    assert not history.can_go_forward
    assert history.back() == "b"


def test_segments_of_something_in_the_home_folder_start_at_home():
    home = GLib.get_home_dir()
    inside = Gio.File.new_for_path(home + "/Projects/island")

    segments = locations.segments(inside)

    assert [name for name, _ in segments] == ["Home", "Projects", "island"]
    assert path_of(segments[0][1]) == home
    assert [name for name, _ in locations.segments(locations.home())] == ["Home"]
