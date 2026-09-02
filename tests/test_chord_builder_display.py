"""Tests for issue #98's Chord builder screen -- reel-stepping/typeahead
pure logic. Per this repo's test convention, `render()`'s actual screen
layout is smoke-tested manually, not here."""

import chord_builder_display as cbd
from score_editor_state import EditorColumn, EditorNote


def _col(*pcs_octs):
    return EditorColumn(notes=[EditorNote(pitch_class=pc, octave=oc) for pc, oc in pcs_octs],
                         duration_class="quarter")


# --- state_from_column / notes_from_state -------------------------------

def test_state_from_column_seeds_root_from_lowest_note():
    column = _col((7, 4))  # G4 alone
    state = cbd.state_from_column(column)
    assert state.root_pc == 7
    assert state.base_octave == 4
    assert state.third_token == "none"
    assert state.fifth_token == "none"
    assert state.seventh_token == "none"


def test_state_from_column_detects_existing_degrees():
    # G major triad: G(root), B(major 3rd, +4), D(perfect 5th, +7).
    column = _col((7, 4), (11, 4), (2, 5))
    state = cbd.state_from_column(column)
    assert state.root_pc == 7
    assert state.third_token == "3"
    assert state.fifth_token == "5"
    assert state.seventh_token == "none"


def test_state_from_column_on_a_rest_defaults_to_c_and_none():
    column = _col()
    state = cbd.state_from_column(column, default_octave=5)
    assert state.root_pc == 0
    assert state.base_octave == 5
    assert state.third_token == state.fifth_token == state.seventh_token == "none"


def test_notes_from_state_builds_root_plus_active_degrees():
    state = cbd.BuilderState(root_pc=0, third_token="3", fifth_token="5", seventh_token="none", base_octave=4)
    notes = cbd.notes_from_state(state)
    pcs_octs = sorted((n.pitch_class, n.octave) for n in notes)
    assert pcs_octs == [(0, 4), (4, 4), (7, 4)]  # C major triad


def test_notes_from_state_carries_octave_across_the_twelfth():
    # A root near the top of the octave with a 7th interval crosses into
    # the next octave (11 + 11 = 22 -> pitch class 10, octave +1).
    state = cbd.BuilderState(root_pc=11, third_token="none", fifth_token="none", seventh_token="7", base_octave=4)
    notes = cbd.notes_from_state(state)
    seventh = next(n for n in notes if n.pitch_class != 11)
    assert (seventh.pitch_class, seventh.octave) == (10, 5)


# --- reel stepping ---------------------------------------------------------

def test_move_slot_wraps_both_directions():
    n = len(cbd.BUILDER_SLOTS)
    assert cbd.move_slot(0, -1) == n - 1
    assert cbd.move_slot(n - 1, 1) == 0


def test_spin_root_follows_circle_of_fifths_order():
    # C (0) -> next in fifths order is G (7).
    assert cbd.spin_root(0, 1) == 7


def test_spin_root_wraps_around():
    last = cbd.ROOT_REEL[-1]
    assert cbd.spin_root(last, 1) == cbd.ROOT_REEL[0]


def test_spin_degree_wraps_and_steps():
    assert cbd.spin_degree("none", cbd.THIRD_OPTIONS, 1) == "sus2"
    last_token = cbd.THIRD_OPTIONS[-1][0]
    assert cbd.spin_degree(last_token, cbd.THIRD_OPTIONS, 1) == "none"


def test_spin_quality_wraps_and_returns_key():
    n = len(cbd.QUALITY_PRESETS)
    new_index, key = cbd.spin_quality(n - 1, 1)
    assert new_index == 0
    assert key == cbd.QUALITY_PRESETS[0][0]


def test_apply_quality_preset_fills_degree_reels_not_root():
    state = cbd.BuilderState(root_pc=5, third_token="none", fifth_token="none", seventh_token="none", base_octave=4)
    assert cbd.apply_quality_preset(state, "maj7") is True
    assert (state.third_token, state.fifth_token, state.seventh_token) == ("3", "5", "7")
    assert state.root_pc == 5  # untouched


def test_apply_quality_preset_unknown_key_is_a_noop():
    state = cbd.BuilderState(root_pc=0, third_token="3", fifth_token="5", seventh_token="none", base_octave=4)
    assert cbd.apply_quality_preset(state, "not-a-real-key") is False
    assert (state.third_token, state.fifth_token, state.seventh_token) == ("3", "5", "none")


# --- root typeahead ------------------------------------------------------

def test_root_typeahead_uppercase_letter_jumps_immediately():
    new_root, just_jumped = cbd.step_root_typeahead(0, False, "F")
    assert new_root == 5  # F
    assert just_jumped is True


def test_root_typeahead_lowercase_letter_does_not_jump():
    # Letter matching is exact-case (see the function's own docstring):
    # lowercase 'b' is reserved for the flat accidental, so a lowercase
    # letter keystroke alone must never jump the root.
    new_root, just_jumped = cbd.step_root_typeahead(0, False, "f")
    assert new_root == 0
    assert just_jumped is False


def test_root_typeahead_sharp_nudges_the_just_jumped_root():
    root, just_jumped = cbd.step_root_typeahead(0, False, "F")
    root, just_jumped = cbd.step_root_typeahead(root, just_jumped, "#")
    assert root == 6  # F#
    assert just_jumped is False


def test_root_typeahead_flat_nudges_the_just_jumped_root():
    root, just_jumped = cbd.step_root_typeahead(0, False, "B")
    root, just_jumped = cbd.step_root_typeahead(root, just_jumped, "b")
    assert root == 10  # Bb


def test_root_typeahead_accidental_without_a_prior_jump_is_a_noop():
    root, just_jumped = cbd.step_root_typeahead(3, False, "#")
    assert root == 3
    assert just_jumped is False


def test_root_typeahead_unrelated_key_is_a_noop():
    root, just_jumped = cbd.step_root_typeahead(3, True, "z")
    assert root == 3
    assert just_jumped is False


# --- alias typeahead (quality/degree reels) -------------------------------

def test_alias_typeahead_commits_immediately_when_unambiguous():
    buffer, resolved = cbd.step_alias_typeahead("", "7", cbd.QUALITY_ALIASES)
    assert resolved == "dom7"
    assert buffer == ""


def test_alias_typeahead_buffers_while_ambiguous():
    buffer, resolved = cbd.step_alias_typeahead("", "m", cbd.QUALITY_ALIASES)
    assert resolved is None
    assert buffer == "m"


def test_alias_typeahead_resolves_once_buffer_becomes_unambiguous():
    buffer, resolved = cbd.step_alias_typeahead("", "m", cbd.QUALITY_ALIASES)
    buffer, resolved = cbd.step_alias_typeahead(buffer, "7", cbd.QUALITY_ALIASES)
    assert resolved == "min7"
    assert buffer == ""


def test_alias_typeahead_resets_on_a_dead_end():
    buffer, resolved = cbd.step_alias_typeahead("", "m", cbd.QUALITY_ALIASES)
    buffer, resolved = cbd.step_alias_typeahead(buffer, "z", cbd.QUALITY_ALIASES)
    assert resolved is None
    assert buffer == ""


def test_force_commit_alias_resolves_an_exact_but_ambiguous_buffer():
    assert cbd.force_commit_alias("m", cbd.QUALITY_ALIASES) == "min"


def test_force_commit_alias_none_when_not_an_exact_key():
    assert cbd.force_commit_alias("xyz", cbd.QUALITY_ALIASES) is None


def test_degree_alias_map_excludes_none_token():
    alias_map = cbd.degree_alias_map(cbd.THIRD_OPTIONS)
    assert "none" not in alias_map
    assert alias_map["b3"] == "b3"
