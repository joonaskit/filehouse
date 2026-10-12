import os
import stat

import pytest
from gi.repository import Gio, GLib
from helpers import gfile, make_tree

from filehouse.core.operations import permissions
from filehouse.core.operations.permissions import PRESETS, Outcome

BY_KEY = {preset.key: preset for preset in PRESETS}


def mode(path):
    return stat.S_IMODE(os.lstat(path).st_mode)


def test_the_presets_are_the_four_of_the_design():
    assert [(p.label, oct(p.file_mode)[2:]) for p in PRESETS] == [
        ("Private", "600"),
        ("Shared", "664"),
        ("Make executable", "755"),
        ("Read-only", "444"),
    ]


@pytest.mark.parametrize("preset", PRESETS, ids=lambda preset: preset.key)
def test_a_file_gets_the_mode_of_the_preset(tmp_path, preset):
    path = tmp_path / "file"
    path.write_text("content")
    path.chmod(0o640)

    outcome = permissions.apply(preset, [gfile(path)])

    assert outcome == Outcome(changed=1)
    assert mode(path) == preset.file_mode
    assert path.read_text() == "content"


@pytest.mark.parametrize(
    "key, expected", [("private", 0o700), ("shared", 0o775), ("read-only", 0o555)]
)
def test_a_folder_gets_the_open_bit_with_every_read_bit(tmp_path, key, expected):
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "inside").write_text("")
    (folder / "inside").chmod(0o640)
    try:
        outcome = permissions.apply(BY_KEY[key], [gfile(folder)])

        assert outcome == Outcome(changed=1)
        assert mode(folder) == expected
        # It can still be opened, and what is in it was not touched
        assert os.listdir(folder) == ["inside"]
        assert mode(folder / "inside") == 0o640
    finally:
        folder.chmod(0o700)


def test_make_executable_leaves_folders_alone(tmp_path):
    make_tree(tmp_path, {"folder": {}, "script.sh": "#!/bin/sh"})
    (tmp_path / "folder").chmod(0o700)
    (tmp_path / "script.sh").chmod(0o600)

    outcome = permissions.apply(
        BY_KEY["executable"], [gfile(tmp_path / "folder"), gfile(tmp_path / "script.sh")]
    )

    assert outcome == Outcome(changed=1, skipped=1)
    assert mode(tmp_path / "folder") == 0o700
    assert mode(tmp_path / "script.sh") == 0o755


def test_a_link_is_never_followed_and_is_reported(tmp_path):
    make_tree(
        tmp_path,
        {
            "target": "t",
            "folder": {},
            "link": ("link", "target"),
            "folder link": ("link", "folder"),
            "broken": ("link", "nowhere"),
        },
    )
    (tmp_path / "target").chmod(0o640)
    (tmp_path / "folder").chmod(0o750)

    outcome = permissions.apply(
        BY_KEY["private"], [gfile(tmp_path / name) for name in ("link", "folder link", "broken")]
    )

    assert outcome.changed == 0 and outcome.skipped == 0
    assert [str(error) for error in outcome.errors] == [
        "link: a link has no permissions of its own",
        "folder link: a link has no permissions of its own",
        "broken: a link has no permissions of its own",
    ]
    assert all(error.step == "change permissions" for error in outcome.errors)
    # What they point to is as it was
    assert mode(tmp_path / "target") == 0o640
    assert mode(tmp_path / "folder") == 0o750


def test_a_missing_file_is_named_and_the_rest_is_still_done(tmp_path):
    make_tree(tmp_path, {"a": "a", "c": "c"})
    for name in ("a", "c"):
        (tmp_path / name).chmod(0o640)

    outcome = permissions.apply(
        BY_KEY["read-only"], [gfile(tmp_path / name) for name in ("a", "gone", "c")]
    )

    assert outcome.changed == 2
    assert [str(error) for error in outcome.errors] == ["gone: no such file or folder"]
    assert outcome.errors[0].path == str(tmp_path / "gone")
    assert mode(tmp_path / "a") == mode(tmp_path / "c") == 0o444


def test_a_file_that_may_not_be_changed_is_named_and_the_rest_is_still_done(tmp_path, monkeypatch):
    # As for a file that belongs to someone else: only its owner may change it
    make_tree(tmp_path, {"mine": "", "theirs": "", "mine too": ""})
    for name in ("mine", "theirs", "mine too"):
        (tmp_path / name).chmod(0o640)
    set_mode = permissions._set_mode

    def refuse_theirs(location, new_mode, cancellable):
        if location.get_basename() == "theirs":
            raise GLib.Error.new_literal(
                Gio.io_error_quark(), "Operation not permitted", Gio.IOErrorEnum.PERMISSION_DENIED
            )
        set_mode(location, new_mode, cancellable)

    monkeypatch.setattr(permissions, "_set_mode", refuse_theirs)

    outcome = permissions.apply(
        BY_KEY["shared"], [gfile(tmp_path / name) for name in ("mine", "theirs", "mine too")]
    )

    assert outcome.changed == 2
    assert [str(error) for error in outcome.errors] == ["theirs: permission denied"]
    assert mode(tmp_path / "theirs") == 0o640
    assert mode(tmp_path / "mine") == mode(tmp_path / "mine too") == 0o664


def test_a_folder_keeps_set_group_id_and_sticky_and_a_file_loses_its_special_bits(tmp_path):
    folder = tmp_path / "shared folder"
    folder.mkdir()
    folder.chmod(0o3770)
    program = tmp_path / "program"
    program.write_text("")
    program.chmod(0o4755)
    assert mode(folder) == 0o3770 and mode(program) == 0o4755

    permissions.apply(BY_KEY["shared"], [gfile(folder), gfile(program)])

    assert mode(folder) == 0o3775
    assert mode(program) == 0o664


def test_cancelled_before_it_starts_changes_nothing(tmp_path):
    path = tmp_path / "file"
    path.write_text("")
    path.chmod(0o640)
    cancellable = Gio.Cancellable()
    cancellable.cancel()

    assert permissions.apply(BY_KEY["private"], [gfile(path)], cancellable) == Outcome()
    assert mode(path) == 0o640


def test_nothing_selected_does_nothing():
    assert permissions.apply(BY_KEY["private"], []) == Outcome()
