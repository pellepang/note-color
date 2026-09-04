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


# ---------------------------------------------------------------------------
# Frozen-buffer playback wiring (map #99, ticket #121, decision #109).
# The *what is played* logic lives in tab_playback.py (tested there); these
# cover main.py's own start/stop/scope/failure handling. The worker thread
# is exercised only through the observable handshake below -- no audio
# device is ever opened, same "pure logic unit-tested, real I/O smoke-tested"
# split every run_terminal_* loop already follows.
# ---------------------------------------------------------------------------

import threading
import time

from sound_engine import midi_pitch
from terminal_tab_display import TabEntry


class _FakeEngine:
    def __init__(self):
        self.note_ons = []
        self.note_offs = []
        self.all_off_calls = 0

    def note_on(self, event):
        self.note_ons.append(event)
        return len(self.note_ons)

    def schedule_note_off(self, voice_id, delay_seconds):
        self.note_offs.append((voice_id, delay_seconds))

    def all_notes_off(self):
        self.all_off_calls += 1


class _FakeDisplay:
    """Just enough TabDisplay surface for _handle_playback_key: the retained
    history and the renderer's own view of what is on screen."""

    def __init__(self, entries, visible=None):
        self.entries = list(entries)
        self._visible = list(entries if visible is None else visible)
        self.visible_calls = []

    def visible_entries(self, **kwargs):
        self.visible_calls.append(kwargs)
        return list(self._visible)


def _note(pitch_class, octave=4, duration_class="thirtysecond"):
    return {"pitch_class": pitch_class, "octave": octave, "rgb": (1, 2, 3),
            "label": "X", "duration_class": duration_class}


def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def test_is_playback_key_is_enter_and_only_enter():
    assert main.is_playback_key("\r")
    assert main.is_playback_key("\n")
    for key in (" ", "r", "R", "LEFT", "ENTER", None, ""):
        assert not main.is_playback_key(key)


def test_playback_is_a_no_op_while_the_view_is_live():
    """Live-view sonification is explicitly out of scope (#109 dropped it on
    a measured ~163ms latency), so Enter must do nothing at all unfrozen."""
    state = main.PlaybackState()
    display = _FakeDisplay([TabEntry([_note(0)], None, 0.0)])
    engine = _FakeEngine()
    main._handle_playback_key("\r", False, state, display, lambda: engine)
    assert not state.in_progress and engine.note_ons == [] and display.visible_calls == []


def test_playback_plays_the_visible_columns_full_stack_then_finishes():
    state = main.PlaybackState()
    entries = [
        TabEntry([_note(0), _note(4), _note(7)], "C", 0.0),   # a chord column
        TabEntry([_note(2)], None, 0.02),
    ]
    display = _FakeDisplay(entries)
    engine = _FakeEngine()
    main._handle_playback_key("\r", True, state, display, lambda: engine, bpm=240.0)
    assert state.in_progress and state.note_count == 4
    assert _wait_for(lambda: not state.in_progress)
    assert [e.pitch for e in engine.note_ons] == [
        midi_pitch(0, 4), midi_pitch(4, 4), midi_pitch(7, 4), midi_pitch(2, 4),
    ]
    assert len(engine.note_offs) == 4          # every note-on gets its own note-off arranged
    assert engine.all_off_calls == 1


def test_a_marked_range_scopes_playback_instead_of_what_is_visible():
    state = main.PlaybackState()
    entries = [TabEntry([_note(i)], None, float(i) / 100.0) for i in range(5)]
    display = _FakeDisplay(entries, visible=entries[-1:])
    engine = _FakeEngine()
    main._handle_playback_key("\r", True, state, display, lambda: engine,
                               mark_range=(0.0, 0.02), bpm=240.0)
    assert state.note_count == 3
    assert _wait_for(lambda: not state.in_progress)
    assert [e.pitch for e in engine.note_ons] == [midi_pitch(i, 4) for i in range(3)]


def test_a_second_enter_stops_playback_early():
    state = main.PlaybackState()
    entries = [TabEntry([_note(i)], None, float(i)) for i in range(4)]   # a second apart
    display = _FakeDisplay(entries)
    engine = _FakeEngine()
    main._handle_playback_key("\r", True, state, display, lambda: engine, bpm=240.0)
    assert _wait_for(lambda: len(engine.note_ons) == 1)
    main._handle_playback_key("\r", True, state, display, lambda: engine)
    assert _wait_for(lambda: not state.in_progress)
    assert len(engine.note_ons) == 1           # the remaining columns never sounded
    assert engine.all_off_calls == 1           # ...and the stop still released cleanly


def test_nothing_playable_never_reaches_for_a_sound_engine():
    """A barline-only/silent screen shouldn't open the audio device at all."""
    state = main.PlaybackState()
    display = _FakeDisplay([TabEntry([_note(None)], None, 0.0)])

    def _provider():
        raise AssertionError("the engine must not be requested for an empty scope")

    main._handle_playback_key("\r", True, state, display, _provider)
    assert not state.in_progress and state.unavailable is None


def test_an_unavailable_sound_engine_is_reported_not_raised():
    state = main.PlaybackState()
    display = _FakeDisplay([TabEntry([_note(0)], None, 0.0)])

    def _provider():
        raise RuntimeError("SciPy is required: pip install -e .[synth]")

    main._handle_playback_key("\r", True, state, display, _provider)
    assert not state.in_progress
    assert "SciPy" in state.unavailable

    state2 = main.PlaybackState()
    main._handle_playback_key("\r", True, state2, display, None)
    assert state2.unavailable == "no engine"


def test_wait_until_returns_early_when_stopped():
    stop = threading.Event()
    stop.set()
    assert main._wait_until(time.monotonic() + 10.0, stop) is False
    assert main._wait_until(time.monotonic() - 1.0, threading.Event()) is True


# --- Score editor audition/piano/playback (map #99, ticket #120) ----------

def test_resolve_editor_action_matches_the_new_shifted_defaults():
    # Ticket #120's four new editor bindings all default to a *Shift*ed
    # letter, since plain-letter space is nearly exhausted and piano mode
    # claims a two-octave block of it.
    fake = _FakeKeybindStore()
    assert resolve_editor_action("P", fake) == "piano_mode"
    assert resolve_editor_action("L", fake) == "play_from_cursor"
    assert resolve_editor_action("M", fake) == "metronome_toggle"
    assert resolve_editor_action("A", fake) == "audition_toggle"


def test_the_new_editor_bindings_are_matched_exact_case():
    # The load-bearing rule: 'm' is B on piano mode's keyboard while 'M'
    # is the metronome, so a case-insensitive match would make the two
    # indistinguishable. Same treatment undo/redo already get.
    fake = _FakeKeybindStore()
    for lowered in ("p", "l", "m", "a"):
        assert resolve_editor_action(lowered, fake) is None


def test_the_editor_loop_region_reuses_the_tab_views_own_mark_keys():
    # #108: the editor's loop region is the same '['/']' gesture, applied
    # to columns instead of history timestamps -- not a second vocabulary.
    fake = _FakeKeybindStore()
    assert resolve_editor_action("[", fake) == "mark_range_start"
    assert resolve_editor_action("]", fake) == "mark_range_end"


def test_editor_loop_status_is_blank_until_a_mark_is_placed():
    assert main._editor_loop_status(None, None) == ""


def test_editor_loop_status_shows_a_half_placed_range():
    assert main._editor_loop_status(3, None).startswith("loop=[4,...]")
    assert main._editor_loop_status(None, 3).startswith("loop=[4,...]")


def test_editor_loop_status_is_one_based_and_order_independent():
    # 1-based to match the status line's own col= field; normalized so
    # marking end-before-start reads the same as start-before-end.
    assert main._editor_loop_status(5, 2).startswith("loop=[3,6]")
    assert main._editor_loop_status(2, 5).startswith("loop=[3,6]")


def test_editor_audio_status_names_the_octave_only_in_piano_mode():
    import score_audition

    playback = main._EditorPlayback()
    piano = main._editor_audio_status(score_audition.PIANO_MODE, 3, True, False, object(), playback)
    edit = main._editor_audio_status(score_audition.EDIT_MODE, 3, True, False, object(), playback)
    assert "oct=3-4" in piano
    assert "oct=" not in edit


def test_editor_audio_status_reports_only_the_absence_of_sound():
    import score_audition

    playback = main._EditorPlayback()
    assert "sound=unavailable" in main._editor_audio_status(
        score_audition.EDIT_MODE, 3, True, False, None, playback)
    assert "sound=" not in main._editor_audio_status(
        score_audition.EDIT_MODE, 3, True, False, object(), playback)


def test_editor_audio_status_reflects_the_two_toggles():
    import score_audition

    playback = main._EditorPlayback()
    text = main._editor_audio_status(score_audition.EDIT_MODE, 3, False, True, object(), playback)
    assert "audition=off" in text and "metro=on" in text


# --- _EditorPlayback (ticket #120) ---------------------------------------

class _FakePlaybackEngine:
    """Records what playback asks for. No audio device, same split
    tests/test_playback.py already applies to LiveScheduler."""

    def __init__(self):
        self.note_ons = []
        self.panics = 0
        self._next_id = 0

    def note_on(self, event):
        self._next_id += 1
        self.note_ons.append(event.pitch)
        return self._next_id

    def schedule_note_off(self, voice_id, seconds):
        pass

    def all_notes_off(self):
        self.panics += 1


def _three_note_score():
    from score_editor_state import EditorColumn, EditorNote, EditorScore

    return EditorScore(
        time_signature=(4, 4), key_fifths=0, tempo_bpm=60.0,
        columns=[EditorColumn(notes=[EditorNote(pc, 4)], duration_class="quarter")
                 for pc in (0, 2, 4)],
    )


def _run_playback(score, entries, engine, metronome_on=False, step=0.25, limit=200):
    """Drive _EditorPlayback with a synthetic monotonic clock -- the loop's
    own time.monotonic() calls are the only real-world thing here, and the
    class takes `now` as a parameter precisely so this is testable."""
    playback = main._EditorPlayback()
    playback.start(score, entries, 0.0)
    seen = []
    now = 0.0
    for _ in range(limit):
        seen.append(playback.advance(score, engine, metronome_on, now))
        if not playback.active:
            break
        now += step
    return playback, seen


def test_playback_walks_every_column_once_and_then_stops_itself():
    import score_audition

    score = _three_note_score()
    engine = _FakePlaybackEngine()
    entries = score_audition.build_schedule(score.columns)
    playback, seen = _run_playback(score, entries, engine)
    # 60bpm, quarter notes: one column per second. Every column sounded
    # exactly once, in order, and playback ended by itself.
    assert engine.note_ons == [60, 62, 64]
    assert [i for i in seen if i is not None] == [0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2]
    assert seen[-1] is None
    assert not playback.active
    assert engine.panics == 1          # all_notes_off on the way out


def test_playback_starting_mid_score_keeps_the_absolute_beat_grid():
    import score_audition

    score = _three_note_score()
    engine = _FakePlaybackEngine()
    entries = score_audition.schedule_slice(
        score_audition.build_schedule(score.columns), 1, 2)
    playback, seen = _run_playback(score, entries, engine)
    assert engine.note_ons == [62, 64]
    assert seen[0] == 1                # the playhead starts on the marked column


def test_playback_never_mutates_the_score():
    import copy

    import score_audition

    score = _three_note_score()
    before = copy.deepcopy(score)
    _run_playback(score, score_audition.build_schedule(score.columns), _FakePlaybackEngine())
    assert score == before


def test_the_metronome_only_clicks_when_it_is_switched_on():
    import score_audition

    score = _three_note_score()
    entries = score_audition.build_schedule(score.columns)
    silent = _FakePlaybackEngine()
    _run_playback(score, entries, silent, metronome_on=False)
    clicking = _FakePlaybackEngine()
    _run_playback(score, entries, clicking, metronome_on=True)
    assert len(clicking.note_ons) > len(silent.note_ons)
    assert config.EDITOR_METRONOME_DOWNBEAT_PITCH in clicking.note_ons


def test_stopping_playback_clears_the_playhead_and_panics_the_engine():
    import score_audition

    score = _three_note_score()
    engine = _FakePlaybackEngine()
    playback = main._EditorPlayback()
    playback.start(score, score_audition.build_schedule(score.columns), 0.0)
    playback.advance(score, engine, False, 0.0)
    playback.stop(engine)
    assert not playback.active and playback.playhead is None
    assert engine.panics == 1


def test_starting_playback_with_nothing_to_play_is_a_no_op():
    playback = main._EditorPlayback()
    assert playback.start(_three_note_score(), [], 0.0) is False
    assert not playback.active


def test_a_missing_sound_engine_leaves_the_editor_silent_not_broken():
    # _editor_sound_engine()'s whole posture: no audio device, no SciPy,
    # no anything -- the editor still opens, it just makes no sound.
    class _Boom:
        def ensure_sound_engine(self):
            raise RuntimeError("no audio device")

    assert main._editor_sound_engine(_Boom()) == (None, False)


# --- run_score_editor()'s piano-mode wiring (map #99, ticket #120) --------
#
# The loop itself is smoke-tested manually per this repo's convention, but
# the *dispatch* through it -- which keystroke becomes a note, which
# becomes a column advance, what lands in the undo history -- is exactly
# the kind of wiring the two earlier drafts of this ticket got wrong, and
# it is testable without a terminal: RawKeys is the only thing standing
# between the loop and a real TTY, so a scripted stand-in for it drives
# the whole loop headlessly.

class _ScriptedKeys:
    """A RawKeys stand-in that replays a fixed list of kitty KeyEvents and
    then raises KeyboardInterrupt, so the loop exits on its own."""

    def __init__(self, events, kitty=True):
        self._events = list(events)
        self.kitty = kitty
        self.restored = False

    def poll_event(self):
        if not self._events:
            raise KeyboardInterrupt
        return self._events.pop(0)

    def release_all(self):
        """Real RawKeys hands back synthetic releases for anything it
        believes is still held; this stand-in tracks nothing, so [] is
        the faithful answer (and is exactly what the real one returns on
        a terminal without the protocol)."""
        return []

    def restore(self):
        self.restored = True


def _press(key):
    """A press of `key`. A named key ("TAB", "UP") carries no associated
    text and no codepoint, which is exactly how a real terminal reports
    one; only an ordinary character key has either."""
    import kitty_keys

    single = len(key) == 1
    return kitty_keys.KeyEvent(key=key, event=kitty_keys.PRESS, mods=0,
                               text=key if single else "",
                               codepoint=ord(key) if single else 0)


def _repeat(key):
    import kitty_keys

    return kitty_keys.KeyEvent(key=key, event=kitty_keys.REPEAT, mods=0,
                               text=key, codepoint=ord(key))


def _release(key):
    import kitty_keys

    return kitty_keys.KeyEvent(key=key, event=kitty_keys.RELEASE, mods=0,
                               text=key, codepoint=ord(key))


def _shift(key):
    import kitty_keys

    return kitty_keys.KeyEvent(key=key.lower(), event=kitty_keys.PRESS,
                               mods=kitty_keys.MOD_SHIFT, text=key.upper(),
                               codepoint=ord(key.lower()))


def _shift_arrow(name):
    """Shift held with a *named* key (an arrow), which carries no text of
    its own -- unlike `_shift()`, whose lower/upper pair only makes sense
    for a letter."""
    import kitty_keys

    return kitty_keys.KeyEvent(key=name, event=kitty_keys.PRESS,
                               mods=kitty_keys.MOD_SHIFT, text="", codepoint=0)


def _drive_editor(tmp_path, monkeypatch, events, kitty=True):
    """Runs run_score_editor() over a fresh blank score with a scripted
    key stream, returning the score it was left holding. No TTY, no audio
    device (score_audition.sound_notes() no-ops on a None engine), no
    real sleeping."""
    import score_editor_display as sed
    import score_editor_state as ses

    path = str(tmp_path / "piano.musicxml")
    ses.save_score(ses.new_blank_score(), path)

    # The score is captured off render() rather than off load_score()
    # because undo/redo *rebind* the loop's local `score` to a fresh
    # snapshot -- a reference grabbed at load time would go stale the
    # moment either fired.
    captured = {}

    def spy_render(score, *args, **kwargs):
        captured["score"] = score

    monkeypatch.setattr(sed, "render", spy_render)
    monkeypatch.setattr(main, "RawKeys", lambda *a, **k: _ScriptedKeys(events, kitty=kitty))
    monkeypatch.setattr(main, "_editor_sound_engine", lambda session: (None, False))
    monkeypatch.setattr(main.time, "sleep", lambda dt: None)
    try:
        main.run_score_editor(path)
    except KeyboardInterrupt:
        pass
    return captured["score"]


def _pitches(score):
    return [[(n.pitch_class, n.octave) for n in c.notes] for c in score.columns]


def test_piano_mode_keys_pressed_together_land_in_one_column(tmp_path, monkeypatch):
    # Shift+P enters piano mode; z/c/b held together are C/E/G in the
    # keyboard's base octave (config.EDITOR_PIANO_BASE_OCTAVE) -- one
    # chord, one column, no advance.
    score = _drive_editor(tmp_path, monkeypatch, [
        _shift("p"), _press("z"), _press("c"), _press("b"),
        _release("z"), _release("c"), _release("b"),
    ])
    base = config.EDITOR_PIANO_BASE_OCTAVE
    assert _pitches(score) == [[(0, base), (4, base), (7, base)]]


def test_piano_mode_keys_pressed_in_sequence_fill_successive_columns(tmp_path, monkeypatch):
    score = _drive_editor(tmp_path, monkeypatch, [
        _shift("p"),
        _press("z"), _release("z"),
        _press("x"), _release("x"),
        _press("c"), _release("c"),
    ])
    base = config.EDITOR_PIANO_BASE_OCTAVE
    # A blank score starts with one column, so two more were appended.
    assert _pitches(score) == [[(0, base)], [(2, base)], [(4, base)]]


def test_auto_repeat_never_advances_or_duplicates(tmp_path, monkeypatch):
    # A held key auto-repeats in the kitty protocol. That must place
    # nothing new -- the key is still down, still the same chord.
    score = _drive_editor(tmp_path, monkeypatch, [
        _shift("p"), _press("z"), _repeat("z"), _repeat("z"), _release("z"),
    ])
    assert _pitches(score) == [[(0, config.EDITOR_PIANO_BASE_OCTAVE)]]


def test_letters_stay_editor_commands_outside_piano_mode(tmp_path, monkeypatch):
    # Without Shift+P first, 'z' is zoom_cycle and 'c' is chords_only --
    # neither writes a note. The score must come back untouched.
    score = _drive_editor(tmp_path, monkeypatch, [_press("z"), _press("c")])
    assert _pitches(score) == [[]]


def test_shift_p_leaves_piano_mode_again(tmp_path, monkeypatch):
    score = _drive_editor(tmp_path, monkeypatch, [
        _shift("p"), _press("z"), _release("z"), _shift("p"), _press("x"),
    ])
    # The second 'x' arrived in edit mode, where it is delete_column --
    # and the editor refuses to delete its last remaining column, so the
    # one note played stays put.
    assert _pitches(score) == [[(0, config.EDITOR_PIANO_BASE_OCTAVE)]]


def test_a_degraded_terminal_places_without_advancing(tmp_path, monkeypatch):
    # No kitty protocol -> no key releases -> #108's explicit degraded
    # path: every press joins the current column, arrows move on.
    score = _drive_editor(tmp_path, monkeypatch, [
        _shift("p"), _press("z"), _press("x"), _press("c"),
    ], kitty=False)
    base = config.EDITOR_PIANO_BASE_OCTAVE
    assert _pitches(score) == [[(0, base), (2, base), (4, base)]]


def test_undo_after_piano_entry_restores_the_state_before_the_last_chord(tmp_path, monkeypatch):
    # The bug this guards: the undo snapshot for a new column must be
    # taken *before* the column is appended, or undo restores a score
    # that already carries the empty column the chord was written into.
    # ('u' is a note on the upper row, so undo is reached from edit mode
    # -- an intended consequence of piano entry being a mode.)
    base = config.EDITOR_PIANO_BASE_OCTAVE
    events = [
        _shift("p"),
        _press("z"), _release("z"),
        _press("x"), _release("x"),
        _shift("p"),          # back to edit mode
        _press("u"),          # undo
    ]
    score = _drive_editor(tmp_path, monkeypatch, events)
    assert _pitches(score) == [[(0, base)]]


def test_undoing_every_piano_note_returns_to_the_blank_score(tmp_path, monkeypatch):
    events = [
        _shift("p"),
        _press("z"), _release("z"),
        _press("x"), _release("x"),
        _shift("p"),
        _press("u"), _press("u"),
    ]
    score = _drive_editor(tmp_path, monkeypatch, events)
    assert _pitches(score) == [[]]


# --- run_synth_tool()'s wiring (map #99, ticket #119) ----------------------
#
# Same reasoning as the score editor's piano-mode block above: the render
# loop is smoke-tested manually per this repo's convention, but the
# *dispatch* through it -- which keystroke becomes a note on which MIDI
# channel, which becomes a parameter sweep, what a Tab does to the voice
# budget -- is exactly what an unverified draft gets wrong, and RawKeys is
# the only thing between the loop and a real TTY. THE MACHINE THESE WERE
# WRITTEN ON IS MUTED: nothing below asserts anything was heard, only that
# the right calls were made with the right arguments.

class _ScriptedSynthKeys(_ScriptedKeys):
    """`_ScriptedKeys` with one difference the synth loop needs: it
    reports "no input right now" (None) once the script runs out, before
    interrupting.

    `run_synth_tool()` drains every pending event *then* renders, so a
    stand-in that interrupted the moment the script emptied would leave
    the loop without a single render -- and the render call is where
    these tests read the final state from. A real terminal returns None
    the instant its input buffer is empty, so this is the faithful
    behaviour, not a workaround.
    """

    def __init__(self, events, kitty=True):
        super().__init__(events)
        self.kitty = kitty
        self._idled = False

    def poll_event(self):
        if self._events:
            return self._events.pop(0)
        if not self._idled:
            self._idled = True
            return None
        raise KeyboardInterrupt


class _FakeVoices:
    def active_count(self):
        return 0


class _FakeSound:
    """A `sound_engine.SoundEngine` stand-in that records note-ons instead
    of opening an output device."""

    def __init__(self):
        self.engine = None
        self.voices = _FakeVoices()
        self.note_ons = []          # (pitch, velocity, channel)
        self.released = []
        self.panics = 0
        self.polyphony_override = "unset"
        self._next_id = 0

    def note_on(self, event, velocity=1.0, channel=0, patch=None):
        self.note_ons.append((event.pitch, event.velocity, event.channel))
        self._next_id += 1
        return self._next_id

    def release_voice(self, voice_id):
        self.released.append(voice_id)

    def all_notes_off(self):
        self.panics += 1

    def set_polyphony_override(self, value):
        self.polyphony_override = value


class _FakeSession:
    def __init__(self, sound):
        self._sound = sound

    def ensure_sound_engine(self):
        return self._sound


def _drive_synth(monkeypatch, events, kitty=True):
    """Runs run_synth_tool() with a scripted key stream against a fake
    sound engine. No TTY, no audio device, no real sleeping."""
    import synth_display

    sound = _FakeSound()
    rendered = {}

    def spy_render(state, colors, status, help_legend=""):
        rendered["state"] = state
        rendered["status"] = status
        rendered["colors"] = colors
        rendered["legend"] = help_legend

    monkeypatch.setattr(synth_display, "render", spy_render)
    monkeypatch.setattr(main, "RawKeys", lambda *a, **k: _ScriptedSynthKeys(events, kitty=kitty))
    monkeypatch.setattr(main.time, "sleep", lambda dt: None)
    result = main.run_synth_tool(session=_FakeSession(sound))
    return sound, rendered, result


def _synth_pitches(sound):
    return [pitch for pitch, _vel, _chan in sound.note_ons]


def test_synth_letters_play_notes_from_the_shared_tracker_keyboard(monkeypatch):
    import score_audition
    import sound_engine

    sound, _rendered, _r = _drive_synth(monkeypatch, [
        _press("z"), _press("x"), _press("c"),
    ])
    expected = [sound_engine.midi_pitch(*score_audition.pitch_for_key(k, config.SYNTH_BASE_OCTAVE))
                for k in "zxc"]
    assert _synth_pitches(sound) == expected


def test_synth_plays_at_full_velocity_on_the_note_channel(monkeypatch):
    # #107 decision 3: QWERTY has no dynamics to report, and faking them
    # would be a lie the sampler's own velocity layers then act on.
    import synth_layout

    sound, _r, _res = _drive_synth(monkeypatch, [_press("z")])
    assert sound.note_ons == [(sound.note_ons[0][0], 1.0, synth_layout.NOTE_CHANNEL)]


def test_synth_releases_the_note_when_the_key_comes_up(monkeypatch):
    sound, _r, _res = _drive_synth(monkeypatch, [_press("z"), _release("z")])
    assert len(sound.note_ons) == 1 and sound.released == [1]


def test_synth_swallows_auto_repeat_so_a_held_key_sustains(monkeypatch):
    # A held key machine-gunning is the failure the kitty protocol exists
    # to fix here.
    sound, _r, _res = _drive_synth(monkeypatch, [
        _press("z"), _repeat("z"), _repeat("z"), _release("z"),
    ])
    assert len(sound.note_ons) == 1


def test_synth_pads_play_on_the_drum_channel(monkeypatch):
    import synth_layout

    # Tab twice from the two-octave layout reaches the 4x4 pad square.
    sound, _r, _res = _drive_synth(monkeypatch, [
        _press("TAB"), _press("TAB"), _press("z"),
    ])
    pitch, velocity, channel = sound.note_ons[0]
    assert channel == synth_layout.PAD_CHANNEL
    assert pitch == synth_layout.pad_midi_key(0)
    assert velocity == 1.0


def test_synth_tab_reaches_every_layout_in_turn(monkeypatch):
    names = []
    for taps in range(4):
        _s, rendered, _res = _drive_synth(monkeypatch, [_press("TAB")] * taps)
        names.append(rendered["state"].layout.name)
    assert names == ["two-octave", "octave-pads", "pads", "two-octave"]


def test_synth_arrows_sweep_the_selected_parameter(monkeypatch):
    _s, rendered, _res = _drive_synth(monkeypatch, [
        _press("DOWN"), _press("RIGHT"), _press("RIGHT"),
    ])
    state = rendered["state"]
    # Row 1 of a synth patch's panel is osc1.octave; two Rights from 0.
    assert state.patch.osc1.octave == 2
    assert state.patch_dirty


def test_synth_shift_arrows_make_a_coarse_sweep(monkeypatch):
    _s, fine, _res = _drive_synth(monkeypatch, [_press("DOWN"), _press("RIGHT")])
    _s2, coarse, _res2 = _drive_synth(monkeypatch, [
        _press("DOWN"), _shift_arrow("RIGHT"),
    ])
    assert coarse["state"].patch.osc1.octave > fine["state"].patch.osc1.octave


def test_synth_shift_up_down_transposes_the_whole_keyboard(monkeypatch):
    # The bug the first draft of this loop shipped with: Shift+Up/Down
    # resolved to param_prev/param_next, so shift_octave() was
    # unreachable and `oct=` could never change.
    sound, rendered, _res = _drive_synth(monkeypatch, [
        _press("z"), _shift_arrow("UP"), _press("z"),
    ])
    assert rendered["state"].octave_shift == 1
    before, after = _synth_pitches(sound)
    assert after == before + 12


def test_synth_octave_shift_shows_in_the_status_line(monkeypatch):
    _s, rendered, _res = _drive_synth(monkeypatch, [_shift_arrow("DOWN")])
    assert "oct=-1" in rendered["status"]


def test_synth_status_line_always_says_how_long_notes_last(monkeypatch):
    # #107 point 7: on a terminal reporting no key releases every note is
    # a fixed length, and saying so plainly is what keeps "why won't
    # notes sustain?" from becoming a bug report.
    _s, held, _res = _drive_synth(monkeypatch, [_press("z")], kitty=True)
    assert "keys=held" in held["status"]

    _s2, fixed, _res2 = _drive_synth(monkeypatch, [_press("z")], kitty=False)
    assert "keys=fixed" in fixed["status"] and "no key release" in fixed["status"]


def test_synth_without_key_releases_notes_are_fixed_length(monkeypatch):
    # The degraded path still plays -- refusing to open outside kitty was
    # rejected, because a drum pad works fine with one-shots.
    sound, _r, _res = _drive_synth(monkeypatch, [_press("z"), _press("x")], kitty=False)
    assert len(sound.note_ons) == 2


def test_synth_a_dual_layout_switches_the_voice_budget(monkeypatch):
    import synth_tool

    # The override is a callable so the budget follows Tab presses live.
    _s, rendered, _res = _drive_synth(monkeypatch, [_press("TAB")])
    state = rendered["state"]
    assert state.layout.is_dual
    assert synth_tool.polyphony_for_layout(state.layout) == config.POLYPHONY_SYNTH_DUAL


def test_synth_restores_the_engine_and_budget_on_the_way_out(monkeypatch):
    # The SoundEngine is process-wide (#105): leaving the synth must hand
    # it back exactly as it was found, or every later view plays through
    # the synth's router.
    sound, _r, _res = _drive_synth(monkeypatch, [_press("z")])
    assert sound.engine is None
    assert sound.polyphony_override is None
    assert sound.panics >= 1        # ...and nothing is left sounding


def test_synth_pipe_returns_to_the_menu_with_nothing_left_sounding(monkeypatch):
    sound, _r, result = _drive_synth(monkeypatch, [_press("z"), _press("|")])
    assert result == "menu"
    assert sound.engine is None and sound.panics >= 1


def test_synth_ctrl_c_quits_the_app(monkeypatch):
    _s, _r, result = _drive_synth(monkeypatch, [])
    assert result == "quit"


def test_synth_panic_stops_every_sounding_note(monkeypatch):
    sound, _r, _res = _drive_synth(monkeypatch, [
        _press("z"), _press("x"), _shift("m"),
    ])
    assert len(sound.released) == 2      # both notes let go
    assert sound.panics >= 2             # the panic itself, plus the exit


def test_synth_switching_layouts_never_leaves_a_note_stuck(monkeypatch):
    # A key held across a layout change would otherwise be released
    # against a slot that no longer exists.
    sound, _r, _res = _drive_synth(monkeypatch, [_press("z"), _press("TAB")])
    assert sound.released == [1]


def test_synth_transposing_never_leaves_a_note_stuck(monkeypatch):
    sound, _r, _res = _drive_synth(monkeypatch, [_press("z"), _shift_arrow("UP")])
    assert sound.released == [1]


def test_synth_overlays_open_over_the_panel_and_keep_the_keys_playing(monkeypatch):
    # #107 point 6: an inline overlay, never a separate screen -- the
    # instrument stays on screen and playable underneath.
    import synth_tool

    sound, rendered, _res = _drive_synth(monkeypatch, [_shift("p"), _press("z")])
    assert rendered["state"].overlay.kind == synth_tool.OVERLAY_PATCH
    assert len(sound.note_ons) == 1


def test_synth_typing_in_the_save_overlay_both_types_and_sounds(monkeypatch):
    # Not an oversight: the always-plays invariant holding even where a
    # text field would normally claim the keyboard.
    sound, rendered, _res = _drive_synth(monkeypatch, [
        _shift("w"), _press("z"), _press("x"),
    ])
    assert rendered["state"].overlay.buffer.endswith("zx")
    assert len(sound.note_ons) == 2


def test_synth_esc_closes_an_overlay_without_saving(monkeypatch):
    _s, rendered, _res = _drive_synth(monkeypatch, [_shift("w"), _press("ESC")])
    assert rendered["state"].overlay is None


def test_synth_help_legend_toggles(monkeypatch):
    _s, on, _res = _drive_synth(monkeypatch, [_press("z")])
    assert on["legend"]
    _s2, off, _res2 = _drive_synth(monkeypatch, [_shift("h")])
    assert off["legend"] == ""


def test_synth_legend_advertises_only_reachable_actions(monkeypatch):
    # A legend promising a keybind the dispatcher does not implement is
    # how the octave-shift bug above stayed invisible.
    import kitty_keys
    import synth_tool

    _s, rendered, _res = _drive_synth(monkeypatch, [_press("z")])
    legend = rendered["legend"]
    for key, action in synth_tool.SHIFT_ACTIONS.items():
        if f"shift+{key.lower()}=" in legend:
            assert synth_tool.resolve_action(_shift(key)) == action
    assert "shift+up/down=octave" in legend
    assert synth_tool.resolve_action(
        kitty_keys.KeyEvent("UP", kitty_keys.PRESS, kitty_keys.MOD_SHIFT, "", 0)) == "octave_up"


def test_synth_binding_keys_needs_a_custom_layout_first(monkeypatch):
    # Built-ins are never edited in place -- Shift+N is one press away.
    _s, builtin, _res = _drive_synth(monkeypatch, [_press("z"), _shift("b")])
    assert builtin["state"].layout.builtin
    assert builtin["state"].layout.slot_for("z").kind == "note"

    _s2, custom, _res2 = _drive_synth(monkeypatch, [_shift("n"), _press("z"), _shift("b")])
    assert not custom["state"].layout.builtin
    assert custom["state"].layout.slot_for("z").kind == "pad"


def test_synth_auto_repeat_types_one_character_in_the_save_overlay(monkeypatch):
    # A held key sustains one note, so it must likewise type one
    # character rather than a run of them.
    _s, rendered, _res = _drive_synth(monkeypatch, [
        _shift("w"), _press("z"), _repeat("z"), _repeat("z"),
    ])
    assert rendered["state"].overlay.buffer.endswith("z")
    assert not rendered["state"].overlay.buffer.endswith("zz")


def test_synth_an_unbound_key_can_still_be_bound_back(monkeypatch):
    # "Point at a key" means "play it", so an unbound key that never
    # became the bind target could never be bound to anything again.
    _s, rendered, _res = _drive_synth(monkeypatch, [
        _shift("n"),                       # custom layout
        _press("z"), _shift("b"), _shift("b"),   # note -> pad -> unbound
        _press("z"), _shift("b"),          # ...and back round to note
    ])
    assert rendered["state"].layout.slot_for("z").kind == "note"
