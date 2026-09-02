"""Tests for main.py's pure, non-threaded helper logic. Mirrors this
repo's existing convention (see test_shell.py) of importing and directly
testing small pure functions extracted from main.py's render loops,
rather than the loops themselves (smoke-tested manually)."""

import pytest

import config
from config_store import ConfigStore
from rhythm_reanalysis import HopRecord

import main
from main import (
    _filter_hop_records_to_range, _handle_mark_keys, _handle_property_key, _hop_beats, _mark_range,
    _parse_csi_params, _parse_property_input, _property_field_texts, resolve_editor_action,
)


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


def test_resolve_editor_action_shift_arrows_are_hardcoded_transpose():
    # Issue #98 follow-up: transpose moved off a remappable '+'/'-' onto
    # hardcoded Shift+Up/Shift+Down (the SHIFT_UP/SHIFT_DOWN tokens
    # RawKeys.poll() returns for that CSI sequence) -- same tier as
    # Left/Right/Up/Down/Enter, never consulting the keybind store at all.
    fake = _FakeKeybindStore()
    assert resolve_editor_action("SHIFT_UP", fake) == "transpose_up"
    assert resolve_editor_action("SHIFT_DOWN", fake) == "transpose_down"


def test_resolve_editor_action_plusminus_no_longer_transpose():
    # '+'/'-' were transpose_up/transpose_down's old default keybinds --
    # confirms they're genuinely gone from the remappable table now, not
    # just no longer the *default* (they're not in _EDITOR_ACTIONS at
    # all, so no remap could bring them back onto '+'/'-' either).
    fake = _FakeKeybindStore()
    assert resolve_editor_action("+", fake) is None
    assert resolve_editor_action("-", fake) is None


def test_resolve_editor_action_matches_default_keybinds():
    fake = _FakeKeybindStore()
    assert resolve_editor_action(" ", fake) == "note_toggle"
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


# --- _parse_csi_params() (issue #98 follow-up: Shift+Up/Down transpose) ----

def test_parse_csi_params_bare_arrow_returns_plain_direction():
    # No parameter bytes at all -- 'ESC [ <letter>', today's original and
    # by far most common case (an ordinary unmodified arrow press). Must
    # keep working exactly as before this function existed.
    assert _parse_csi_params("", "A") == "UP"
    assert _parse_csi_params("", "B") == "DOWN"
    assert _parse_csi_params("", "C") == "RIGHT"
    assert _parse_csi_params("", "D") == "LEFT"


def test_parse_csi_params_shift_modifier_returns_shift_tokens():
    # 'ESC [ 1 ; 2 <letter>' -- xterm's standard Shift-modifier encoding.
    assert _parse_csi_params("1;2", "A") == "SHIFT_UP"
    assert _parse_csi_params("1;2", "B") == "SHIFT_DOWN"


def test_parse_csi_params_unrecognized_params_falls_back_to_plain_direction():
    # Some other modifier code (Alt/Ctrl/combinations) this app doesn't
    # have a use for yet -- falls back to the plain direction rather than
    # dropping the keystroke, same graceful-degradation posture as a
    # laggy/multiplexed pty's arrow-burst handling.
    assert _parse_csi_params("1;5", "A") == "UP"  # Ctrl+Up, unrecognized here
    assert _parse_csi_params("1;2", "C") == "RIGHT"  # Shift+Right, not consumed by this app


def test_parse_csi_params_unknown_final_byte_is_none():
    assert _parse_csi_params("", "Z") is None
    assert _parse_csi_params("1;2", "Z") is None


# --- Inline header editor (score_properties, 't', issue #98 follow-up) ----

class _FakeScore:
    """A minimal stand-in for score_editor_state.EditorScore exposing just
    the three fields the inline header editor touches -- avoids pulling
    in music21 (a real EditorScore's module) just to test this dispatch
    logic."""

    def __init__(self, time_signature=(4, 4), key_fifths=0, tempo_bpm=90.0):
        self.time_signature = time_signature
        self.key_fifths = key_fifths
        self.tempo_bpm = tempo_bpm


def test_property_field_texts_reflects_current_score_values():
    score = _FakeScore(time_signature=(3, 4), key_fifths=2, tempo_bpm=120.0)
    texts = _property_field_texts(score)
    assert texts["time_signature"] == "time=3/4"
    assert texts["key_signature"] == "key=2 sharps"
    assert texts["tempo"] == "tempo=120"


def test_parse_property_input_tempo_clamps_into_range():
    import score_properties_display as spd
    assert _parse_property_input("tempo", "150") == 150.0
    assert _parse_property_input("tempo", "9999") == spd.TEMPO_MAX_BPM
    assert _parse_property_input("tempo", "0") == spd.TEMPO_MIN_BPM


def test_parse_property_input_time_signature_parses_nd():
    assert _parse_property_input("time_signature", "3/4") == (3, 4)
    assert _parse_property_input("time_signature", "11/8") == (11, 8)


def test_parse_property_input_time_signature_rejects_malformed_text():
    with pytest.raises(ValueError):
        _parse_property_input("time_signature", "not-a-signature")
    with pytest.raises(ValueError):
        _parse_property_input("time_signature", "0/4")


def test_parse_property_input_empty_buffer_is_none():
    assert _parse_property_input("tempo", "") is None
    assert _parse_property_input("time_signature", "  ") is None


def test_handle_property_key_left_right_move_the_highlighted_field():
    import score_properties_display as spd
    score = _FakeScore()
    slot, buffer, still_editing = _handle_property_key("RIGHT", score, 0, "")
    assert slot == spd.move_slot(0, 1)
    assert buffer == ""
    assert still_editing is True
    slot, buffer, still_editing = _handle_property_key("LEFT", score, slot, "")
    assert slot == 0


def test_handle_property_key_up_down_spin_the_highlighted_fields_value():
    import score_properties_display as spd
    score = _FakeScore(tempo_bpm=100.0)
    slot = spd.PROPERTY_SLOTS.index("tempo")
    _handle_property_key("UP", score, slot, "")
    assert score.tempo_bpm == 100.0 + spd.TEMPO_STEP_BPM
    _handle_property_key("DOWN", score, slot, "")
    assert score.tempo_bpm == 100.0


def test_handle_property_key_digits_accumulate_into_buffer_on_typable_fields():
    score = _FakeScore()
    slot = 2  # tempo
    _, buffer, still_editing = _handle_property_key("1", score, slot, "")
    _, buffer, still_editing = _handle_property_key("2", score, slot, buffer)
    _, buffer, still_editing = _handle_property_key("0", score, slot, buffer)
    assert buffer == "120"
    assert still_editing is True
    # Not applied to the score until Enter.
    assert score.tempo_bpm == 90.0


def test_handle_property_key_key_signature_slot_ignores_typed_digits():
    import score_properties_display as spd
    score = _FakeScore()
    slot = spd.PROPERTY_SLOTS.index("key_signature")
    _, buffer, _ = _handle_property_key("5", score, slot, "")
    assert buffer == ""


def test_handle_property_key_backspace_trims_the_buffer():
    score = _FakeScore()
    _, buffer, _ = _handle_property_key("\x7f", score, 2, "120")
    assert buffer == "12"


def test_handle_property_key_enter_applies_a_pending_buffer_and_exits():
    score = _FakeScore(tempo_bpm=90.0)
    slot = 2  # tempo
    _, buffer, still_editing = _handle_property_key("\r", score, slot, "140")
    assert score.tempo_bpm == 140.0
    assert buffer == ""
    assert still_editing is False


def test_handle_property_key_enter_with_no_buffer_just_exits():
    score = _FakeScore(tempo_bpm=90.0)
    _, _, still_editing = _handle_property_key("\r", score, 2, "")
    assert score.tempo_bpm == 90.0
    assert still_editing is False


def test_handle_property_key_enter_with_unparseable_buffer_leaves_value_unchanged():
    score = _FakeScore(time_signature=(4, 4))
    slot = 0  # time_signature
    _, _, still_editing = _handle_property_key("\r", score, slot, "garbage")
    assert score.time_signature == (4, 4)
    assert still_editing is False


def test_handle_property_key_navigation_resets_the_buffer():
    score = _FakeScore()
    _, buffer, _ = _handle_property_key("LEFT", score, 2, "42")
    assert buffer == ""
