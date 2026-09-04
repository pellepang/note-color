"""Tests for issue #98's main score-editor view -- pure cursor/mutation
logic and viewport math. Per this repo's test convention, `render()`'s
actual screen layout is smoke-tested manually, not here (see the module
docstring)."""

import score_editor_display as sed
from score_editor_state import EditorColumn, EditorNote
from staff_map import staff_row


def _col(*pcs_octs, duration_class="quarter"):
    return EditorColumn(notes=[EditorNote(pitch_class=pc, octave=oc) for pc, oc in pcs_octs],
                         duration_class=duration_class)


# --- pitch_at_row / staff_row inverse -----------------------------------

def test_pitch_at_row_is_inverse_of_staff_row_for_naturals():
    for pitch_class in (0, 2, 4, 5, 7, 9, 11):  # every natural pitch class
        for octave in range(2, 6):
            row = staff_row(pitch_class, octave)
            assert sed.pitch_at_row(row) == (pitch_class, octave)


# --- pitch_at_row / _legend_letter key-signature awareness (issue #98
# follow-up: default placement/legend should reflect the active key) ------

def test_pitch_at_row_defaults_to_natural_with_no_key_signature():
    row = staff_row(5, 4)  # F4
    assert sed.pitch_at_row(row, key_fifths=0) == (5, 4)


def test_pitch_at_row_sharps_the_implied_letter_in_g_major():
    # G major (key_fifths=1) sharps F -- the F row's default placement
    # should be F# (pitch class 6), same octave.
    row = staff_row(5, 4)  # the F row
    assert sed.pitch_at_row(row, key_fifths=1) == (6, 4)


def test_pitch_at_row_flats_the_implied_letter_in_f_major():
    # F major (key_fifths=-1) flats B -- the B row's default placement
    # should be Bb (pitch class 10), same octave.
    row = staff_row(11, 4)  # the B row
    assert sed.pitch_at_row(row, key_fifths=-1) == (10, 4)


def test_pitch_at_row_unaffected_letters_stay_natural():
    # G major only sharps F -- every other row's default is still natural.
    row = staff_row(0, 4)  # the C row
    assert sed.pitch_at_row(row, key_fifths=1) == (0, 4)


def test_legend_letter_shows_the_key_signature_accidental():
    row = staff_row(5, 4)  # the F row
    assert sed._legend_letter(row, key_fifths=0) == "F"
    assert sed._legend_letter(row, key_fifths=1) == "F♯"


def test_legend_letter_shows_flat_accidental():
    row = staff_row(11, 4)  # the B row
    assert sed._legend_letter(row, key_fifths=-1) == "B♭"


# --- clamping ------------------------------------------------------------

def test_clamp_row_stays_within_staff_bounds():
    from staff_map import TOP_ROW, BOTTOM_ROW
    assert sed.clamp_row(TOP_ROW + 5) == TOP_ROW
    assert sed.clamp_row(BOTTOM_ROW - 5) == BOTTOM_ROW
    assert sed.clamp_row(10) == 10


def test_clamp_column_stays_within_score_bounds():
    assert sed.clamp_column(-1, 5) == 0
    assert sed.clamp_column(5, 5) == 4
    assert sed.clamp_column(2, 5) == 2


# --- note_index_at_row -----------------------------------------------------

def test_note_index_at_row_finds_matching_note():
    column = _col((0, 4), (4, 4))
    row_c = staff_row(0, 4)
    assert sed.note_index_at_row(column, row_c) == 0


def test_note_index_at_row_none_when_nothing_there():
    column = _col((0, 4))
    assert sed.note_index_at_row(column, staff_row(4, 4)) is None


# --- note_toggle -----------------------------------------------------------

def test_toggle_note_places_a_natural_when_row_is_empty():
    column = _col((0, 4))
    row = staff_row(4, 4)  # E4, empty
    assert sed.toggle_note_at_cursor(column, row) is True
    assert (4, 4) in [(n.pitch_class, n.octave) for n in column.notes]


def test_toggle_note_removes_an_existing_note_when_column_has_others():
    column = _col((0, 4), (4, 4))
    row = staff_row(4, 4)
    assert sed.toggle_note_at_cursor(column, row) is True
    assert [(n.pitch_class, n.octave) for n in column.notes] == [(0, 4)]


def test_toggle_note_can_empty_a_column_to_zero_notes():
    # Issue #98 follow-up (direct user feedback after hands-on use):
    # Space used to refuse to remove a column's very last note, forcing a
    # separate 'r'/clear_to_rest press -- that two-step flow was unwanted
    # friction, so Space now empties a column all the way down to a Rest
    # just like it removes any other note.
    column = _col((0, 4))
    row = staff_row(0, 4)
    assert sed.toggle_note_at_cursor(column, row) is True
    assert column.notes == []


def test_toggle_note_places_the_key_implied_spelling_by_default():
    # Issue #98 follow-up: placing a new note in G major (key_fifths=1)
    # on the F row should default to F#, not bare F.
    column = _col()
    row = staff_row(5, 4)  # the F row
    assert sed.toggle_note_at_cursor(column, row, key_fifths=1) is True
    assert [(n.pitch_class, n.octave) for n in column.notes] == [(6, 4)]


def test_toggle_note_on_a_rest_column_creates_the_first_note():
    column = _col()
    row = staff_row(7, 4)
    assert sed.toggle_note_at_cursor(column, row) is True
    assert [(n.pitch_class, n.octave) for n in column.notes] == [(7, 4)]


# --- transpose ---------------------------------------------------------

def test_transpose_up_shifts_a_semitone_and_returns_new_row():
    column = _col((0, 4))  # C4
    row = staff_row(0, 4)
    new_row = sed.transpose_note_at_cursor(column, row, +1)
    assert (column.notes[0].pitch_class, column.notes[0].octave) == (1, 4)  # C#4/Db4
    assert new_row == staff_row(1, 4)


def test_transpose_down_crosses_octave_boundary():
    column = _col((0, 4))  # C4
    row = staff_row(0, 4)
    sed.transpose_note_at_cursor(column, row, -1)
    assert (column.notes[0].pitch_class, column.notes[0].octave) == (11, 3)  # B3


def test_transpose_is_a_noop_when_nothing_at_row():
    column = _col((0, 4))
    result = sed.transpose_note_at_cursor(column, staff_row(4, 4), +1)
    assert result is None
    assert [(n.pitch_class, n.octave) for n in column.notes] == [(0, 4)]


# --- duration cycling ------------------------------------------------------

def test_cycle_duration_shorten_moves_toward_shorter_values():
    from duration_tracker import DURATION_CLASS_ORDER
    column = _col((0, 4), duration_class="quarter")
    sed.cycle_duration(column, +1)
    idx = DURATION_CLASS_ORDER.index("quarter")
    assert column.duration_class == DURATION_CLASS_ORDER[idx + 1]


def test_cycle_duration_lengthen_moves_toward_longer_values():
    from duration_tracker import DURATION_CLASS_ORDER
    column = _col((0, 4), duration_class="quarter")
    sed.cycle_duration(column, -1)
    idx = DURATION_CLASS_ORDER.index("quarter")
    assert column.duration_class == DURATION_CLASS_ORDER[idx - 1]


def test_cycle_duration_clamps_at_the_whole_note_end():
    column = _col((0, 4), duration_class="whole")
    sed.cycle_duration(column, -1)
    assert column.duration_class == "whole"


def test_cycle_duration_clamps_at_the_thirtysecond_end():
    column = _col((0, 4), duration_class="thirtysecond")
    sed.cycle_duration(column, +1)
    assert column.duration_class == "thirtysecond"


# --- clear_to_rest -----------------------------------------------------

def test_clear_to_rest_empties_the_column():
    column = _col((0, 4), (4, 4))
    sed.clear_to_rest(column)
    assert column.notes == []


# --- insert/delete columns ---------------------------------------------

def test_insert_column_at_inserts_a_rest_before_index():
    from score_editor_state import new_blank_score
    score = new_blank_score()
    score.columns.append(_col((0, 4)))
    sed.insert_column_at(score, 0)
    assert len(score.columns) == 3
    assert score.columns[0].notes == []


def test_delete_column_at_removes_and_returns_true():
    from score_editor_state import new_blank_score
    score = new_blank_score()
    score.columns.append(_col((0, 4)))
    assert sed.delete_column_at(score, 0) is True
    assert len(score.columns) == 1


def test_delete_column_at_refuses_the_last_column():
    from score_editor_state import new_blank_score
    score = new_blank_score()
    assert len(score.columns) == 1
    assert sed.delete_column_at(score, 0) is False
    assert len(score.columns) == 1


# --- zoom ----------------------------------------------------------------

def test_cycle_zoom_wraps_around():
    n = len(sed.ZOOM_LEVELS)
    level = 0
    for _ in range(n):
        level = sed.cycle_zoom(level)
    assert level == 0


# --- visible_column_range ------------------------------------------------

def test_visible_column_range_shows_everything_when_it_fits():
    assert sed.visible_column_range(2, 5, 10) == (0, 5)


def test_visible_column_range_centers_on_cursor():
    start, end = sed.visible_column_range(10, 30, 6)
    assert start <= 10 < end
    assert end - start == 6


def test_visible_column_range_clamps_at_left_edge():
    start, end = sed.visible_column_range(0, 30, 6)
    assert start == 0
    assert end == 6


def test_visible_column_range_clamps_at_right_edge():
    start, end = sed.visible_column_range(29, 30, 6)
    assert start == 24
    assert end == 30


# --- chord_name_for_column -------------------------------------------------

def test_chord_name_for_column_blank_for_a_single_note():
    column = _col((0, 4))
    assert sed.chord_name_for_column(column) is None


def test_chord_name_for_column_blank_for_a_rest():
    column = _col()
    assert sed.chord_name_for_column(column) is None


def test_chord_name_for_column_recognizes_a_major_triad():
    # C-E-G, a plain root-position C major triad.
    column = _col((0, 4), (4, 4), (7, 4))
    name = sed.chord_name_for_column(column)
    assert name is not None
    assert "C" in name


# --- place_note_at_pitch / append_column (map #99, ticket #120) -----------

def test_place_note_at_pitch_adds_the_exact_pitch_and_returns_its_row():
    column = _col()
    row = sed.place_note_at_pitch(column, 6, 4)   # F#4 -- no key signature involved
    assert [(n.pitch_class, n.octave) for n in column.notes] == [(6, 4)]
    assert row == staff_row(6, 4)


def test_place_note_at_pitch_keeps_a_column_in_ascending_pitch_order():
    column = _col((7, 4))
    sed.place_note_at_pitch(column, 0, 4)
    sed.place_note_at_pitch(column, 4, 4)
    assert [(n.pitch_class, n.octave) for n in column.notes] == [(0, 4), (4, 4), (7, 4)]


def test_place_note_at_pitch_is_idempotent_and_never_removes():
    # Pressing the same piano key twice inside one chord group must leave
    # one note, not stack duplicates -- and must never delete, unlike
    # toggle_note_at_cursor(): a played key silently erasing a note would
    # be a trap.
    column = _col((0, 4))
    sed.place_note_at_pitch(column, 0, 4)
    sed.place_note_at_pitch(column, 0, 4)
    assert [(n.pitch_class, n.octave) for n in column.notes] == [(0, 4)]


def test_append_column_adds_an_empty_column_inheriting_a_duration():
    from score_editor_state import new_blank_score

    score = new_blank_score()
    index = sed.append_column(score, "eighth")
    assert index == len(score.columns) - 1
    assert score.columns[index].notes == []
    assert score.columns[index].duration_class == "eighth"


def test_append_column_falls_back_to_the_default_duration():
    from duration_tracker import DEFAULT_DURATION_CLASS
    from score_editor_state import new_blank_score

    score = new_blank_score()
    index = sed.append_column(score)
    assert score.columns[index].duration_class == DEFAULT_DURATION_CLASS
