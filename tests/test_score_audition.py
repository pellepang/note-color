"""Score editor audition / piano mode / playback / metronome (wayfinder
map #99, ticket #120, decision #108).

Everything asserted here is pure logic -- the keyboard map, the mode
state machine, the press-together/press-in-sequence grouping, the beat
schedule and playhead, the loop-region arithmetic, the metronome grid --
plus the two thin audio helpers, exercised against a fake engine that
records calls. `main.run_score_editor()`'s own interactive loop is
smoke-tested manually, per this repo's standing convention; nothing here
opens an audio device or a terminal.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import kitty_keys  # noqa: E402
import score_audition as sa  # noqa: E402
from score_editor_state import EditorColumn, EditorNote, EditorScore  # noqa: E402


def column(pitches, duration_class="quarter"):
    return EditorColumn(notes=[EditorNote(pc, octave) for pc, octave in pitches],
                        duration_class=duration_class)


def score_of(*columns, time_signature=(4, 4), tempo_bpm=120.0):
    return EditorScore(time_signature=time_signature, key_fifths=0,
                       tempo_bpm=tempo_bpm, columns=list(columns))


# ---------------------------------------------------------------- keyboard

def test_layout_covers_two_full_octaves_with_no_duplicate_keys():
    assert len(sa.PIANO_LOWER_ROW) == 12
    assert len(sa.PIANO_UPPER_ROW) == 12
    assert len(sa.PIANO_KEY_SEMITONES) == 24
    assert sorted(sa.PIANO_KEY_SEMITONES.values()) == list(range(24))


def test_lower_row_is_a_chromatic_run_from_c():
    # z = C, s = C#, x = D ... m = B, in the tracker layout #107 settled.
    assert sa.pitch_for_key("z", 3) == (0, 3)
    assert sa.pitch_for_key("s", 3) == (1, 3)
    assert sa.pitch_for_key("x", 3) == (2, 3)
    assert sa.pitch_for_key("m", 3) == (11, 3)


def test_upper_row_is_the_octave_above():
    assert sa.pitch_for_key("q", 3) == (0, 4)
    assert sa.pitch_for_key("u", 3) == (11, 4)
    assert sa.pitch_for_key("7", 3) == (10, 4)


def test_base_octave_shifts_everything_together():
    assert sa.pitch_for_key("z", 4) == (0, 4)
    assert sa.pitch_for_key("u", 4) == (11, 5)


def test_a_key_off_the_keyboard_is_not_a_note():
    assert sa.pitch_for_key("p", 3) is None
    assert sa.pitch_for_key("LEFT", 3) is None


def test_base_octave_is_clamped_leaving_room_for_the_upper_row():
    assert sa.clamp_base_octave(config.MIN_OCTAVE - 3) == config.MIN_OCTAVE
    assert sa.clamp_base_octave(config.MAX_OCTAVE + 3) == config.MAX_OCTAVE - 1


# -------------------------------------------------------------------- mode

def test_mode_toggles_both_ways():
    assert sa.toggle_mode(sa.EDIT_MODE) == sa.PIANO_MODE
    assert sa.toggle_mode(sa.PIANO_MODE) == sa.EDIT_MODE


def test_letters_are_only_notes_inside_piano_mode():
    assert sa.is_piano_note_event(sa.PIANO_MODE, "m", 0)
    assert not sa.is_piano_note_event(sa.EDIT_MODE, "m", 0)


def test_a_modified_press_is_never_a_note():
    # The load-bearing case: 'm' is B, Shift+M is the metronome toggle.
    assert not sa.is_piano_note_event(sa.PIANO_MODE, "m", kitty_keys.MOD_SHIFT)
    assert not sa.is_piano_note_event(sa.PIANO_MODE, "z", kitty_keys.MOD_CTRL)


def test_caps_and_num_lock_do_not_suppress_a_note():
    assert sa.is_piano_note_event(sa.PIANO_MODE, "z", kitty_keys.MOD_CAPS_LOCK)
    assert sa.is_piano_note_event(sa.PIANO_MODE, "z", kitty_keys.MOD_NUM_LOCK)


# ------------------------------------------------- press-together grouping

def test_keys_pressed_together_stay_in_one_column():
    entry = sa.PianoEntry(advance_between_groups=True)
    assert entry.press("z") == sa.SAME_COLUMN
    assert entry.press("c") == sa.SAME_COLUMN
    assert entry.press("b") == sa.SAME_COLUMN
    assert entry.held == {"z", "c", "b"}


def test_keys_pressed_in_sequence_fill_successive_columns():
    entry = sa.PianoEntry(advance_between_groups=True)
    assert entry.press("z") == sa.SAME_COLUMN     # first note lands where the cursor is
    assert entry.release("z") is True
    assert entry.press("x") == sa.NEW_COLUMN
    assert entry.release("x") is True
    assert entry.press("c") == sa.NEW_COLUMN


def test_a_group_only_ends_once_every_key_is_up():
    entry = sa.PianoEntry(advance_between_groups=True)
    entry.press("z")
    entry.press("c")
    assert entry.release("z") is False            # 'c' still held -- same chord
    assert entry.press("b") == sa.SAME_COLUMN
    assert entry.release("c") is False
    assert entry.release("b") is True
    assert entry.press("v") == sa.NEW_COLUMN


def test_reset_makes_the_next_press_land_on_the_current_column():
    entry = sa.PianoEntry(advance_between_groups=True)
    entry.press("z")
    entry.release("z")
    entry.reset()                                  # e.g. the caller moved the cursor
    assert entry.press("x") == sa.SAME_COLUMN


def test_degraded_terminal_never_advances_on_its_own():
    entry = sa.PianoEntry(advance_between_groups=False)
    for key in "zxcv":
        assert entry.press(key) == sa.SAME_COLUMN
    # ...and each press still reads as a freshly-started group, so the
    # caller records one undo snapshot per note rather than only the first.
    assert entry.held == {"v"}


# --------------------------------------------------------------- audition

def test_horizontal_movement_auditions_the_whole_column():
    col = column([(0, 4), (4, 4), (7, 4)])
    assert sa.audition_targets("RIGHT", col, 10) == [(0, 4), (4, 4), (7, 4)]
    assert sa.audition_targets("LEFT", col, 10) == [(0, 4), (4, 4), (7, 4)]


def test_vertical_movement_only_auditions_a_note_actually_on_that_row():
    from staff_map import staff_row

    col = column([(0, 4)])
    assert sa.audition_targets("UP", col, staff_row(0, 4)) == [(0, 4)]
    assert sa.audition_targets("DOWN", col, staff_row(0, 4) + 1) == []


def test_a_non_movement_action_auditions_nothing():
    assert sa.audition_targets("save", column([(0, 4)]), 10) == []


# --------------------------------------------------------------- schedule

def test_schedule_accumulates_absolute_beat_positions():
    entries = sa.build_schedule([column([], "quarter"), column([], "half"),
                                 column([], "eighth")])
    assert [e.start_beat for e in entries] == [0.0, 1.0, 3.0]
    assert [e.beats for e in entries] == [1.0, 2.0, 0.5]
    assert [e.index for e in entries] == [0, 1, 2]


def test_playhead_walks_the_schedule_and_then_reports_finished():
    entries = sa.build_schedule([column([], "quarter")] * 3)
    assert sa.playhead_index(entries, 0.0) == 0
    assert sa.playhead_index(entries, 0.99) == 0
    assert sa.playhead_index(entries, 1.0) == 1
    assert sa.playhead_index(entries, 2.5) == 2
    assert sa.playhead_index(entries, 3.0) is None


def test_due_entries_fire_each_column_exactly_once_across_frames():
    entries = sa.build_schedule([column([], "quarter")] * 4)
    fired = []
    previous = float("-inf")
    for now in [0.0, 0.4, 0.9, 1.3, 2.2, 3.7]:
        fired += [e.index for e in sa.due_entries(entries, previous, now)]
        previous = now
    assert fired == [0, 1, 2, 3]


def test_beats_and_seconds_round_trip_against_tempo():
    assert sa.beats_to_seconds(2.0, 120.0) == pytest.approx(1.0)
    assert sa.seconds_to_beats(1.0, 120.0) == pytest.approx(2.0)
    assert sa.duration_seconds("half", 60.0) == pytest.approx(2.0)


def test_a_nonsense_tempo_does_not_divide_by_zero():
    assert sa.beats_to_seconds(1.0, 0.0) > 0


# ------------------------------------------------------------ loop region

def test_without_a_loop_playback_runs_from_the_cursor_to_the_end():
    assert sa.playback_range(6, 2, None) == (2, 5)


def test_a_marked_loop_ends_playback_at_the_region_end():
    assert sa.playback_range(10, 4, (3, 7)) == (4, 7)


def test_a_cursor_outside_the_loop_starts_at_the_region_start():
    assert sa.playback_range(10, 0, (3, 7)) == (3, 7)
    assert sa.playback_range(10, 9, (3, 7)) == (3, 7)


def test_loop_marks_are_order_independent():
    assert sa.playback_range(10, 5, (7, 3)) == sa.playback_range(10, 5, (3, 7))


def test_an_empty_score_has_nothing_to_play():
    assert sa.playback_range(0, 0, None) is None


def test_schedule_slice_selects_only_the_played_columns_keeping_absolute_beats():
    entries = sa.build_schedule([column([], "quarter")] * 5)
    played = sa.schedule_slice(entries, 2, 3)
    assert [e.index for e in played] == [2, 3]
    assert played[0].start_beat == 2.0     # absolute, not re-zeroed


# ------------------------------------------------------------- metronome

def test_metronome_clicks_once_per_quarter_in_four_four():
    clicks = sa.metronome_clicks(0.0, 4.0, (4, 4))
    assert [beat for beat, _ in clicks] == [0.0, 1.0, 2.0, 3.0]
    assert [down for _, down in clicks] == [True, False, False, False]


def test_metronome_downbeat_lands_on_the_real_bar_not_the_start_column():
    # Playback starting mid-bar (beat 2 of a 4/4 bar) must still put its
    # downbeat on beat 4, where the bar genuinely falls.
    clicks = sa.metronome_clicks(2.0, 6.0, (4, 4))
    assert [beat for beat, _ in clicks] == [2.0, 3.0, 4.0, 5.0]
    assert [down for _, down in clicks] == [False, False, True, False]


def test_compound_time_clicks_on_the_denominator_note_value():
    click_beats, per_bar = sa.beat_grid((6, 8))
    assert (click_beats, per_bar) == (0.5, 6)
    clicks = sa.metronome_clicks(0.0, 3.0, (6, 8))
    assert len(clicks) == 6
    assert clicks[0][1] is True
    assert not any(down for _, down in clicks[1:])


def test_due_clicks_use_the_same_half_open_window_as_columns():
    clicks = sa.metronome_clicks(0.0, 4.0, (4, 4))
    assert [b for b, _ in sa.due_clicks(clicks, float("-inf"), 0.0)] == [0.0]
    assert [b for b, _ in sa.due_clicks(clicks, 0.0, 2.5)] == [1.0, 2.0]


# ---------------------------------------------------- the thin audio edge

class FakeEngine:
    """Records what the editor asks for, so the note/duration decisions can
    be asserted without an audio device -- the same "pure logic tested, no
    hardware I/O" split tests/test_playback.py already applies to
    LiveScheduler."""

    def __init__(self):
        self.note_ons = []
        self.offs = []
        self.panics = 0
        self._next_id = 0

    def note_on(self, event):
        self._next_id += 1
        self.note_ons.append(event)
        return self._next_id

    def schedule_note_off(self, voice_id, seconds):
        self.offs.append((voice_id, seconds))

    def all_notes_off(self):
        self.panics += 1


def test_sounding_a_chord_starts_one_voice_per_note_with_a_matching_note_off():
    engine = FakeEngine()
    ids = sa.sound_notes(engine, [(0, 4), (4, 4), (7, 4)], 0.5)
    assert len(ids) == 3
    assert [e.pitch for e in engine.note_ons] == [60, 64, 67]   # C4/E4/G4, MIDI
    assert [seconds for _, seconds in engine.offs] == [0.5, 0.5, 0.5]


def test_a_negative_duration_never_reaches_the_engine():
    engine = FakeEngine()
    sa.sound_notes(engine, [(0, 4)], -1.0)
    assert engine.offs == [(1, 0.0)]


def test_no_engine_means_silence_not_a_crash():
    assert sa.sound_notes(None, [(0, 4)], 0.5) == []
    assert sa.sound_metronome_click(None, True) is None


def test_the_downbeat_click_is_higher_than_the_other_beats():
    engine = FakeEngine()
    sa.sound_metronome_click(engine, True)
    sa.sound_metronome_click(engine, False)
    assert engine.note_ons[0].pitch > engine.note_ons[1].pitch
    assert all(seconds == config.EDITOR_METRONOME_CLICK_SECONDS for _, seconds in engine.offs)


# ------------------------------------------------------- appended columns

def test_an_appended_column_inherits_the_current_duration():
    columns = [column([], "eighth"), column([], "sixteenth")]
    assert sa.new_column_duration(columns, 1) == "sixteenth"
    assert sa.new_column_duration(columns, 9) == "sixteenth"   # past the end
    assert sa.new_column_duration([], 0) == "quarter"
