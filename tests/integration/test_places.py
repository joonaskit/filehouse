"""The places file Dolphin keeps: read for the user's own, and edited without harming the rest."""

import os
import xml.etree.ElementTree as ET

import pytest

from filehouse.integration.places import PlacesFile

KDE = "http://www.kde.org"
NS = {"bookmark": "http://www.freedesktop.org/standards/desktop-bookmarks"}

# What Dolphin writes, cut down: system items, a hidden place, one of the user's, and the
# separators that keep the place of a drive
SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xbel>
<xbel xmlns:bookmark="http://www.freedesktop.org/standards/desktop-bookmarks"\
 xmlns:kdepriv="http://www.kde.org/kdepriv"\
 xmlns:mime="http://www.freedesktop.org/standards/shared-mime-info">
 <info>
  <metadata owner="http://www.kde.org">
   <kde_places_version>4</kde_places_version>
   <GroupState-Places-IsHidden>false</GroupState-Places-IsHidden>
  </metadata>
 </info>
 <bookmark href="file:///home/me">
  <title>Home</title>
  <info>
   <metadata owner="http://freedesktop.org">
    <bookmark:icon name="user-home"/>
   </metadata>
   <metadata owner="http://www.kde.org">
    <ID>1789116079/0</ID>
    <isSystemItem>true</isSystemItem>
   </metadata>
  </info>
 </bookmark>
 <bookmark href="trash:/">
  <title>Trash</title>
  <info>
   <metadata owner="http://freedesktop.org">
    <bookmark:icon name="user-trash"/>
   </metadata>
   <metadata owner="http://www.kde.org">
    <ID>1789116079/5</ID>
    <isSystemItem>true</isSystemItem>
   </metadata>
  </info>
 </bookmark>
 <bookmark href="file:///home/me/Secret">
  <title>Secret</title>
  <info>
   <metadata owner="http://freedesktop.org">
    <bookmark:icon name="folder"/>
   </metadata>
   <metadata owner="http://www.kde.org">
    <ID>1790000000/11</ID>
    <isSystemItem>false</isSystemItem>
    <IsHidden>true</IsHidden>
   </metadata>
  </info>
 </bookmark>
 <bookmark href="file:///home/me/code">
  <title>Code</title>
  <info>
   <metadata owner="http://freedesktop.org">
    <bookmark:icon name="folder-development"/>
   </metadata>
   <metadata owner="http://www.kde.org">
    <ID>1790000000/12</ID>
    <isSystemItem>false</isSystemItem>
   </metadata>
  </info>
 </bookmark>
 <separator>
  <info>
   <metadata owner="http://www.kde.org">
    <UDI>/org/freedesktop/UDisks2/block_devices/nvme0n1p2</UDI>
    <isSystemItem>true</isSystemItem>
   </metadata>
  </info>
 </separator>
</xbel>
"""


@pytest.fixture
def path(tmp_path):
    file = tmp_path / "user-places.xbel"
    file.write_text(SAMPLE)
    return str(file)


def titles(places):
    return [place.title for place in places.read()]


def test_the_users_places_are_read_and_the_system_and_hidden_ones_are_not(path):
    found = PlacesFile(path).read()

    assert [(p.title, p.uri, p.icon, p.id) for p in found] == [
        ("Code", "file:///home/me/code", "folder-development", "1790000000/12")
    ]


def test_without_a_file_there_is_nothing_and_nothing_is_made(tmp_path):
    places = PlacesFile(str(tmp_path / "user-places.xbel"))

    assert not places.exists
    assert places.read() is None
    assert not places.add("file:///home/me/x", "X")
    assert not os.path.exists(places.path)


def test_a_file_that_cannot_be_read_is_left_alone(tmp_path):
    file = tmp_path / "user-places.xbel"
    file.write_text("this is not xml")
    places = PlacesFile(str(file))

    assert places.read() is None
    assert not places.add("file:///home/me/x", "X")
    assert not places.remove("1/1")
    assert file.read_text() == "this is not xml"


def test_a_place_is_added_after_the_last_bookmark_and_before_the_separators(path):
    places = PlacesFile(path)

    assert places.add("file:///home/me/Pictures/Shots", "Shots", "folder")

    root = ET.parse(path).getroot()
    tags = [el.tag for el in root]
    assert tags == ["info", "bookmark", "bookmark", "bookmark", "bookmark", "bookmark", "separator"]
    added = [el for el in root.iterfind("bookmark")][-1]
    assert added.get("href") == "file:///home/me/Pictures/Shots"
    assert added.findtext("title") == "Shots"
    assert titles(places) == ["Code", "Shots"]


def test_the_new_place_is_made_as_kde_makes_one(path):
    PlacesFile(path).add("file:///home/me/x", "X")

    root = ET.parse(path).getroot()
    added = list(root.iterfind("bookmark"))[-1]
    kde = [m for m in added.iterfind("info/metadata") if m.get("owner") == KDE][0]
    assert kde.findtext("isSystemItem") == "false"
    time_part, _, number = kde.findtext("ID").partition("/")
    # One above the highest there was
    assert time_part.isdigit() and number == "13"
    assert added.find("info/metadata/bookmark:icon", NS).get("name") == "folder"


def test_what_is_not_touched_is_kept(path):
    places = PlacesFile(path)
    before = ET.parse(path).getroot()

    places.add("file:///home/me/x", "X")
    [place] = [p for p in places.read() if p.title == "X"]
    places.remove(place.id)

    after = ET.parse(path).getroot()
    assert ET.tostring(after) == ET.tostring(before)
    text = open(path).read()
    # The way KDE writes it
    assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xbel>\n')
    for prefix in ("bookmark", "kdepriv", "mime"):
        assert f"xmlns:{prefix}=" in text.partition(">\n <info>")[0]
    assert '<bookmark:icon name="user-home"/>' in text
    assert "<kde_places_version>4</kde_places_version>" in text
    assert "block_devices/nvme0n1p2" in text


def test_a_place_that_is_there_is_not_added_twice(path):
    places = PlacesFile(path)

    assert not places.add("file:///home/me/code", "Code again")
    assert titles(places) == ["Code"]


def test_ids_do_not_repeat(path):
    places = PlacesFile(path)
    places.add("file:///home/me/a", "A")
    places.add("file:///home/me/b", "B")

    ids = [place.id for place in places.read()]

    assert len(set(ids)) == 3
    assert [i.partition("/")[2] for i in ids] == ["12", "13", "14"]


def test_a_place_is_renamed_and_removed_by_its_id(path):
    places = PlacesFile(path)

    assert places.rename("1790000000/12", "Projects")
    assert titles(places) == ["Projects"]
    assert places.remove("1790000000/12")
    assert places.read() == []
    assert not places.remove("1790000000/12")
    assert not places.rename("no/such", "x")


def test_a_system_item_cannot_be_removed_through_here(path):
    places = PlacesFile(path)

    assert not places.remove("1789116079/0")
    assert not places.rename("1789116079/5", "Rubbish")
    assert "Home" in open(path).read() and "Trash" in open(path).read()


def test_a_place_moves_past_its_neighbour(path):
    places = PlacesFile(path)
    places.add("file:///home/me/a", "A")
    places.add("file:///home/me/b", "B")
    ids = {p.title: p.id for p in places.read()}

    assert places.move(ids["B"], -1)
    assert titles(places) == ["Code", "B", "A"]
    assert places.move(ids["Code"], 1)
    assert titles(places) == ["B", "Code", "A"]
    # Not past the ends
    assert not places.move(ids["B"], -1)
    assert not places.move(ids["A"], 1)
    assert titles(places) == ["B", "Code", "A"]
    # The system items did not move
    tags = [el.findtext("title") for el in ET.parse(path).getroot().iterfind("bookmark")]
    assert tags[:2] == ["Home", "Trash"]


def test_the_file_keeps_its_permissions_and_no_temporary_file_stays(path, tmp_path):
    os.chmod(path, 0o640)

    PlacesFile(path).add("file:///home/me/x", "X")

    assert os.stat(path).st_mode & 0o777 == 0o640
    assert os.listdir(tmp_path) == ["user-places.xbel"]


def test_titles_with_markup_are_kept_whole(path):
    places = PlacesFile(path)

    places.add("file:///home/me/a&b", 'A & <B> "C"')

    assert [p.title for p in places.read()][-1] == 'A & <B> "C"'
