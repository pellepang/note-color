"""Frozen-buffer playback's pure half (map #99, ticket #121, decision
#109): which columns are in scope, how a column's detected stack expands
into note events, and how each event's onset/length is computed.

Nothing here opens an audio device or a terminal -- `main.py`'s
`_playback_worker()` (the timing thread) and `TabDisplay.render()` stay
smoke-tested-only, per this repo's standing convention.
"""

import config
import tab_playback
from duration_tracker import DEFAULT_DURATION_CLASS
from sound_engine import midi_pitch
from terminal_tab_display import BarlineEntry, TabEntry


def _note(pitch_class, octave=4, duration_class=None):
    return {
        "pitch_class": pitch_class,
        "octave": octave,
        "rgb": (10, 20, 30),
        "label": "X",
        "duration_class": duration_class,
    }


def _column(t, notes, chord_name=None):
    return TabEntry(list(notes), chord_name, t)


# -- scope selection -------------------------------------------------------

def test_no_marks_plays_exactly_the_visible_columns():
    visible = [_column(1.0, [_note(0)]), _column(2.0, [_note(4)])]
    offscreen = [_column(0.0, [_note(7)])]
    columns = tab_playback.select_columns(visible, offscreen + visible, mark_range=None)
    assert [c.t for c in columns] == [1.0, 2.0]


def test_marked_range_wins_over_what_is_visible_and_is_inclusive():
    all_entries = [_column(t, [_note(0)]) for t in (0.5, 1.0, 2.0, 3.0, 4.5)]
    visible = all_entries[-1:]
    columns = tab_playback.select_columns(visible, all_entries, mark_range=(1.0, 3.0))
    assert [c.t for c in columns] == [1.0, 2.0, 3.0]


def test_barlines_and_silence_columns_are_never_playable():
    entries = [
        _column(0.0, [_note(None)]),        # a 'fix'-mode column pushed during silence
        BarlineEntry(0.5),
        _column(1.0, []),                    # a chord-mode column with an empty stack
        _column(1.5, [_note(3)]),
    ]
    columns = tab_playback.select_columns(entries, entries, mark_range=None)
    assert [c.t for c in columns] == [1.5]
    assert tab_playback.playable_notes(BarlineEntry(0.5)) == []


def test_a_mixed_column_keeps_only_its_real_notes():
    entry = _column(0.0, [_note(None), _note(5), _note(9)])
    assert [n["pitch_class"] for n in tab_playback.playable_notes(entry)] == [5, 9]


# -- note length -----------------------------------------------------------

def test_duration_class_converts_against_the_tempo():
    # A quarter at 120bpm is half a second; a half note is twice that.
    assert tab_playback.note_duration_seconds("quarter", 120.0) == 0.5
    assert tab_playback.note_duration_seconds("half", 120.0) == 1.0
    assert tab_playback.note_duration_seconds("dotted-quarter", 120.0) == 0.75


def test_unfinalized_duration_falls_back_to_the_same_class_render_draws():
    assert (tab_playback.note_duration_seconds(None, 120.0)
            == tab_playback.note_duration_seconds(DEFAULT_DURATION_CLASS, 120.0))


def test_missing_bpm_falls_back_to_the_configured_default_tempo():
    assert (tab_playback.note_duration_seconds("quarter", None)
            == tab_playback.note_duration_seconds("quarter", config.TAB_PLAYBACK_DEFAULT_BPM))
    assert (tab_playback.note_duration_seconds("quarter", 0.0)
            == tab_playback.note_duration_seconds("quarter", config.TAB_PLAYBACK_DEFAULT_BPM))


def test_note_length_is_clamped_at_both_ends():
    assert tab_playback.note_duration_seconds("thirtysecond", 600.0) == config.TAB_PLAYBACK_MIN_NOTE_SECONDS
    assert tab_playback.note_duration_seconds("whole", 1.0) == config.TAB_PLAYBACK_MAX_NOTE_SECONDS


# -- schedule construction -------------------------------------------------

def test_onsets_are_rebased_so_playback_starts_immediately():
    columns = [_column(10.0, [_note(0)]), _column(10.75, [_note(2)]), _column(12.0, [_note(4)])]
    schedule = tab_playback.build_schedule(columns, bpm=120.0)
    assert [n.start_seconds for n in schedule] == [0.0, 0.75, 2.0]


def test_chord_column_expands_to_the_full_detected_stack_each_with_its_own_duration():
    columns = [_column(0.0, [
        _note(0, 4, "quarter"), _note(4, 4, "eighth"), _note(7, 5, "half"),
    ])]
    schedule = tab_playback.build_schedule(columns, bpm=120.0)
    assert [n.pitch for n in schedule] == [midi_pitch(0, 4), midi_pitch(4, 4), midi_pitch(7, 5)]
    assert all(n.start_seconds == 0.0 for n in schedule)          # simultaneous, as detected
    assert [n.duration_seconds for n in schedule] == [0.5, 0.25, 1.0]


def test_every_note_carries_the_configured_fixed_velocity():
    schedule = tab_playback.build_schedule([_column(0.0, [_note(0)])], bpm=120.0)
    assert schedule[0].velocity == config.TAB_PLAYBACK_VELOCITY
    assert tab_playback.build_schedule([_column(0.0, [_note(0)])], bpm=120.0, velocity=0.3)[0].velocity == 0.3


def test_empty_scope_yields_an_empty_schedule_of_zero_length():
    assert tab_playback.build_schedule([]) == []
    assert tab_playback.schedule_duration([]) == 0.0


def test_schedule_duration_covers_the_last_note_s_own_end_not_just_its_onset():
    columns = [_column(0.0, [_note(0, 4, "quarter")]), _column(1.0, [_note(2, 4, "half")])]
    schedule = tab_playback.build_schedule(columns, bpm=120.0)
    assert tab_playback.schedule_duration(schedule) == 1.0 + 1.0


def test_pitch_class_and_octave_ride_along_beside_the_midi_pitch():
    schedule = tab_playback.build_schedule([_column(0.0, [_note(11, 2)])], bpm=120.0)
    assert (schedule[0].pitch_class, schedule[0].octave) == (11, 2)
    assert schedule[0].pitch == midi_pitch(11, 2)
