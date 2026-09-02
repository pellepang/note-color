"""Tests for main.py's pure, non-threaded helper logic. Mirrors this
repo's existing convention (see test_shell.py) of importing and directly
testing small pure functions extracted from main.py's render loops,
rather than the loops themselves (smoke-tested manually)."""

import pytest

import config
from config_store import ConfigStore
from rhythm_reanalysis import HopRecord

import main
from main import _filter_hop_records_to_range, _handle_mark_keys, _hop_beats, _mark_range, resolve_editor_action


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """_handle_mark_keys() consults store.keybind() for mark_range_start/
    mark_range_end -- isolate it from the real ~/.config/note-color/
    config.toml the module-level singleton normally points at, same
    pattern test_settings_display.py already establishes, so these tests
    aren't at the mercy of whatever the dev machine's own config happens
    to remap those actions to."""
    fresh = ConfigStore(path=str(tmp_path / "config.toml"))
    monkeypatch.setattr(main, "store", fresh)
    return fresh


def test_hop_beats_takes_max_not_sum_of_simultaneous_finalizations():
    # Issue #76: an ordinary single note finalized independently by both
    # the mono DurationTracker and multipitch's one-note "chord" must
    # credit its duration once toward the bar boundary, not twice.
    assert _hop_beats([2.0, 2.0]) == 2.0


def test_hop_beats_takes_max_across_differing_values():
    assert _hop_beats([1.5, 3.0, 0.5]) == 3.0


def test_hop_beats_treats_none_entries_as_zero():
    # A `beats` value is None when bpm_estimate was unknown at
    # finalization time -- shouldn't crash max()/comparisons.
    assert _hop_beats([None, 1.0]) == 1.0
    assert _hop_beats([None, None]) == 0.0


def test_hop_beats_empty_list_is_zero():
    # No note finalized this hop at all.
    assert _hop_beats([]) == 0.0


def test_hop_beats_single_mono_only_value_unchanged():
    # No chord/multipitch finalization this hop -- behaves like before
    # the fix for the common single-note case.
    assert _hop_beats([1.25]) == 1.25


# --- _handle_mark_keys() / _mark_range() (loop/section markers) --------

def test_mark_start_key_sets_start_leaves_end_unchanged():
    start, end = _handle_mark_keys("[", True, None, 5.0, timestamp=2.0)
    assert (start, end) == (2.0, 5.0)


def test_mark_end_key_sets_end_leaves_start_unchanged():
    start, end = _handle_mark_keys("]", True, 2.0, None, timestamp=5.0)
    assert (start, end) == (2.0, 5.0)


def test_mark_keys_are_no_op_when_not_frozen():
    # A live-scrolling tail has no stable "point in history" to mark --
    # same gating as scrollback/reanalysis themselves.
    start, end = _handle_mark_keys("[", False, None, None, timestamp=2.0)
    assert (start, end) == (None, None)


def test_mark_keys_are_no_op_with_no_timestamp():
    # Nothing pushed to the display yet -- nothing to mark.
    start, end = _handle_mark_keys("[", True, None, None, timestamp=None)
    assert (start, end) == (None, None)


def test_mark_keys_ignore_unrelated_key():
    start, end = _handle_mark_keys("x", True, 1.0, 2.0, timestamp=9.0)
    assert (start, end) == (1.0, 2.0)


def test_mark_keys_ignore_none_key():
    start, end = _handle_mark_keys(None, True, 1.0, 2.0, timestamp=9.0)
    assert (start, end) == (1.0, 2.0)


def test_mark_range_none_until_both_ends_set():
    assert _mark_range(None, None) is None
    assert _mark_range(2.0, None) is None
    assert _mark_range(None, 5.0) is None


def test_mark_range_normalizes_regardless_of_press_order():
    # mark_range_end pressed before mark_range_start still yields (lo, hi).
    assert _mark_range(5.0, 2.0) == (2.0, 5.0)
    assert _mark_range(2.0, 5.0) == (2.0, 5.0)


# --- _filter_hop_records_to_range() -------------------------------------

def _hop(index, t_field=None):
    # HopRecord's own fields (mono/chord_notes/chroma_novelty) don't
    # matter for this filter -- only hop_index does.
    return HopRecord(hop_index=index, mono=None, chord_notes=(), chroma_novelty=0.0)


def test_filter_returns_records_unchanged_when_no_range_marked():
    records = [_hop(0), _hop(1), _hop(2)]
    assert _filter_hop_records_to_range(records, None, hop_seconds=0.1) == records


def test_filter_keeps_only_hops_inside_inclusive_range():
    hop_seconds = 0.1
    records = [_hop(i) for i in range(10)]  # timestamps 0.0 .. 0.9
    kept = _filter_hop_records_to_range(records, (0.2, 0.5), hop_seconds)
    assert [r.hop_index for r in kept] == [2, 3, 4, 5]


def test_filter_range_boundaries_are_inclusive():
    hop_seconds = 1.0
    records = [_hop(0), _hop(1), _hop(2)]
    kept = _filter_hop_records_to_range(records, (1.0, 2.0), hop_seconds)
    assert [r.hop_index for r in kept] == [1, 2]


def test_filter_range_with_no_matching_hops_is_empty_not_a_crash():
    hop_seconds = 0.1
    records = [_hop(i) for i in range(5)]  # timestamps 0.0 .. 0.4
    kept = _filter_hop_records_to_range(records, (10.0, 20.0), hop_seconds)
    assert kept == []


# --- resolve_editor_action() (issue #98, score editor) --------------------

class _FakeKeybindStore:
    """A minimal stand-in for config_store.ConfigStore exposing just the
    one method resolve_editor_action() needs -- lets these tests inject
    specific bindings directly rather than writing through a real
    ConfigStore/tmp_path config file."""

    def __init__(self, overrides=None):
        self._keybinds = dict(config.DEFAULT_KEYBINDS)
        if overrides:
            self._keybinds.update(overrides)

    def keybind(self, action):
        return self._keybinds[action]


def test_resolve_editor_action_arrow_keys_are_hardcoded():
    fake = _FakeKeybindStore()
    for arrow in ("LEFT", "RIGHT", "UP", "DOWN"):
        assert resolve_editor_action(arrow, fake) == arrow


def test_resolve_editor_action_enter_is_hardcoded():
    fake = _FakeKeybindStore()
    assert resolve_editor_action("\r", fake) == "ENTER"
    assert resolve_editor_action("\n", fake) == "ENTER"


def test_resolve_editor_action_matches_default_keybinds():
    fake = _FakeKeybindStore()
    assert resolve_editor_action(" ", fake) == "note_toggle"
    assert resolve_editor_action("+", fake) == "transpose_up"
    assert resolve_editor_action("-", fake) == "transpose_down"
    assert resolve_editor_action(",", fake) == "duration_shorten"
    assert resolve_editor_action(".", fake) == "duration_lengthen"
    assert resolve_editor_action("r", fake) == "clear_to_rest"
    assert resolve_editor_action("i", fake) == "insert_column"
    assert resolve_editor_action("x", fake) == "delete_column"
    assert resolve_editor_action("z", fake) == "zoom_cycle"
    assert resolve_editor_action("c", fake) == "chords_only_toggle"
    assert resolve_editor_action("w", fake) == "save"
    assert resolve_editor_action("t", fake) == "score_properties"


def test_resolve_editor_action_none_for_an_unbound_key():
    fake = _FakeKeybindStore()
    assert resolve_editor_action("q", fake) is None
    assert resolve_editor_action(None, fake) is None


def test_resolve_editor_action_most_actions_match_case_insensitively():
    fake = _FakeKeybindStore()
    # 'r' (clear_to_rest) also fires on 'R' -- same case-insensitive
    # convention every other remappable keybind in this app already uses.
    assert resolve_editor_action("R", fake) == "clear_to_rest"


def test_resolve_editor_action_undo_redo_are_case_sensitive():
    fake = _FakeKeybindStore()
    assert resolve_editor_action("u", fake) == "undo"
    assert resolve_editor_action("U", fake) == "redo"
    # Lowercase 'u' must never also resolve to redo (its default is the
    # uppercase 'U') -- if it did, undo/redo couldn't coexist as distinct
    # actions sharing one letter.
    assert resolve_editor_action("u", fake) != "redo"


def test_resolve_editor_action_honors_remapped_keybinds():
    fake = _FakeKeybindStore({"save": "y"})
    assert resolve_editor_action("y", fake) == "save"
    assert resolve_editor_action("w", fake) is None


def test_resolve_editor_action_defaults_to_module_level_store(monkeypatch):
    fresh = ConfigStore(path="/nonexistent/path/should/not/be/read.toml")
    monkeypatch.setattr(main, "store", fresh)
    assert resolve_editor_action(" ") == "note_toggle"
