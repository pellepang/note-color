"""Tests for issue #98's live-menu score editor picker -- pure file-list/
selection logic. Per this repo's test convention, `run_score_editor_picker()`'s
actual interactive poll/render loop and `capture_filename()`'s keystroke
loop are smoke-tested manually, not here."""

import os

import score_editor_picker as pk


def test_score_file_paths_finds_musicxml_and_xml(tmp_path):
    (tmp_path / "song.musicxml").write_text("x")
    (tmp_path / "other.xml").write_text("x")
    (tmp_path / "ignored.txt").write_text("x")
    paths = pk.score_file_paths(str(tmp_path))
    names = sorted(os.path.basename(p) for p in paths)
    assert names == ["other.xml", "song.musicxml"]


def test_score_file_paths_empty_directory(tmp_path):
    assert pk.score_file_paths(str(tmp_path)) == []


def test_build_menu_entries_appends_new_score_row():
    entries = pk.build_menu_entries(["/a/one.musicxml", "/a/two.xml"])
    assert entries == ["one.musicxml", "two.xml", "New score..."]


def test_build_menu_entries_with_no_files_still_has_new_score_row():
    assert pk.build_menu_entries([]) == ["New score..."]


def test_move_wraps_both_directions():
    assert pk.move(0, -1, 3) == 2
    assert pk.move(2, 1, 3) == 0


def test_is_new_score_row_true_only_for_the_trailing_row():
    paths = ["/a/one.musicxml"]
    assert pk.is_new_score_row(0, paths) is False
    assert pk.is_new_score_row(1, paths) is True


def test_resolve_new_score_path_appends_default_extension():
    path = pk.resolve_new_score_path("/dir", "myscore")
    assert path == os.path.join("/dir", "myscore.musicxml")


def test_resolve_new_score_path_keeps_an_existing_musicxml_or_xml_extension():
    assert pk.resolve_new_score_path("/dir", "myscore.xml") == os.path.join("/dir", "myscore.xml")
    assert pk.resolve_new_score_path("/dir", "myscore.musicxml") == os.path.join("/dir", "myscore.musicxml")


def test_resolve_new_score_path_falls_back_to_default_name_when_blank():
    assert pk.resolve_new_score_path("/dir", "   ") == os.path.join("/dir", pk.DEFAULT_NEW_SCORE_NAME)
