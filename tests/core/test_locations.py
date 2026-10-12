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


def test_an_item_is_shown_in_the_folder_it_is_in(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.txt").write_text("a")

    file = locations.resolve_item(str(tmp_path / "docs" / "a.txt"))
    folder = locations.resolve_item((tmp_path / "docs").as_uri())
    gone = locations.resolve_item(str(tmp_path / "docs" / "gone.txt"))

    assert (file.location.get_path(), file.select) == (str(tmp_path / "docs"), "a.txt")
    # A folder too: it is the item, not the place to open
    assert (folder.location.get_path(), folder.select) == (str(tmp_path), "docs")
    # Not there any more: its folder is still the place to look
    assert (gone.location.get_path(), gone.select) == (str(tmp_path / "docs"), "gone.txt")


def test_the_root_has_no_folder_to_be_shown_in():
    root = locations.resolve_item("/")

    assert root.location.get_path() == "/" and root.select is None


def test_the_nearest_folder_that_is_there(tmp_path):
    (tmp_path / "there").mkdir()
    (tmp_path / "there" / "file.txt").write_text("f")
    there = Gio.File.new_for_path(str(tmp_path / "there"))

    assert locations.nearest_folder(there).equal(there)
    assert locations.nearest_folder(there.get_child("gone").get_child("deeper")).equal(there)
    # A file is not a folder to open: the one it is in
    assert locations.nearest_folder(there.get_child("file.txt")).equal(there)
    assert locations.nearest_folder(Gio.File.new_for_uri("no-such-scheme://host/a/b")) is None


def test_the_name_of_an_entry_below_a_folder(tmp_path):
    folder = Gio.File.new_for_path(str(tmp_path))

    assert locations.name_below(folder, folder.get_child("a.txt")) == "a.txt"
    assert locations.name_below(folder, folder.get_child("sub").get_child("a.txt")) == "sub/a.txt"
    assert locations.name_below(folder.get_child("sub"), folder.get_child("a.txt")) is None
