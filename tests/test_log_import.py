"""Quantized session-log import (map #99, ticket #122, decision #110).

`quantize_columns()` and the grid helpers are pure -- plain event dicts
in, plain columns out -- so everything below drives them directly with
hand-built events at exact times, never a recorded file. `import_log()`'s
own file reading is exercised once, round-trip, at the bottom; nothing
here builds a music21 graph beyond `score_from_events()`'s single check
that the editor's own dataclasses come out the far side.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import log_import  # noqa: E402


def _note(t, pc, octave=4, duration=0.5, **extra):
    event = {"t": t, "pc": pc, "octave": octave, "duration_seconds": duration}
    event.update(extra)
    return event


# --- grids ----------------------------------------------------------------

def test_grid_beats_covers_every_offered_grid():
    assert [log_import.grid_beats(name) for name in log_import.GRID_NAMES] == [1.0, 0.5, 0.25, 0.125]


def test_grid_beats_falls_back_rather_than_raising_on_an_unknown_name():
    assert log_import.grid_beats("nonesuch") == log_import.grid_beats(log_import.DEFAULT_GRID)


def test_cycle_grid_clamps_at_both_ends():
    assert log_import.cycle_grid("quarter", -1) == "quarter"
    assert log_import.cycle_grid("thirtysecond", 1) == "thirtysecond"
    assert log_import.cycle_grid("eighth", 1) == "sixteenth"
    assert log_import.cycle_grid("eighth", -1) == "quarter"


def test_default_grid_comes_from_config():
    assert log_import.DEFAULT_GRID == config.IMPORT_DEFAULT_GRID


# --- tempo ----------------------------------------------------------------

def test_tempo_uses_the_logs_own_bpm_estimate_when_it_has_one():
    events = [_note(0.0, 0, bpm_estimate=None), _note(1.0, 2, bpm_estimate=120.0)]
    assert log_import.tempo_from_events(events) == 120.0


def test_tempo_falls_back_to_the_recorders_reference_for_a_played_log():
    # A synth recording writes bpm_estimate null on purpose (#110 point 2),
    # so the fallback is the same figure the recorder derived its own
    # duration_class against -- which is what makes a replay's glyphs and
    # an import's columns agree.
    events = [_note(0.0, 0, bpm_estimate=None, source="played")]
    assert log_import.tempo_from_events(events) == config.PLAYED_NOTE_REFERENCE_BPM


# --- quantization ---------------------------------------------------------

def test_empty_log_yields_no_columns():
    assert log_import.quantize_columns([], 90.0) == []


def test_barline_events_are_not_notes():
    assert log_import.quantize_columns([{"kind": "barline", "t": 1.0}], 90.0) == []


def test_notes_close_together_collapse_into_one_chord_column():
    # Two keys of a chord pressed 20ms apart land on the same grid step,
    # which is exactly session_player.group_columns()'s "same t is one
    # column" rule applied to a grid instead of to exact equality.
    columns = log_import.quantize_columns(
        [_note(0.0, 0), _note(0.02, 4), _note(0.01, 7)], 90.0, "sixteenth")
    assert len(columns) == 1
    assert columns[0].notes == [(0, 4), (7, 4), (4, 4)]


def test_a_repeated_pitch_at_one_quantized_instant_is_one_notehead():
    columns = log_import.quantize_columns([_note(0.0, 0), _note(0.01, 0)], 90.0, "sixteenth")
    assert columns[0].notes == [(0, 4)]


def test_column_duration_runs_to_the_next_onset():
    # 90bpm: one beat is 0.667s. Two notes a beat apart, each sounding
    # for the whole beat, are two quarter-note columns and no rest.
    beat = 60.0 / 90.0
    columns = log_import.quantize_columns(
        [_note(0.0, 0, duration=beat), _note(beat, 2, duration=beat)], 90.0, "sixteenth")
    assert [(c.notes, c.duration_class) for c in columns] == [
        ([(0, 4)], "quarter"), ([(2, 4)], "quarter")]


def test_a_note_that_stopped_early_leaves_a_rest_for_the_silence():
    # Half a beat of sound then half a beat of silence: the editor has no
    # independent onsets, so the gap has to become a real Rest column.
    beat = 60.0 / 90.0
    columns = log_import.quantize_columns(
        [_note(0.0, 0, duration=beat / 2), _note(beat, 2, duration=beat)], 90.0, "sixteenth")
    assert [(c.notes, c.duration_class) for c in columns] == [
        ([(0, 4)], "eighth"), ([], "eighth"), ([(2, 4)], "quarter")]


def test_a_late_first_note_gets_a_leading_rest():
    beat = 60.0 / 90.0
    columns = log_import.quantize_columns([_note(beat, 0, duration=beat)], 90.0, "sixteenth")
    assert columns[0].notes == [] and columns[0].duration_class == "quarter"
    assert columns[1].notes == [(0, 4)]


def test_a_note_shorter_than_the_grid_still_becomes_a_column():
    # Rounding a real note's span to zero steps would silently drop it.
    columns = log_import.quantize_columns([_note(0.0, 0, duration=0.001)], 90.0, "quarter")
    assert [c.notes for c in columns] == [[(0, 4)]]


def test_a_coarser_grid_merges_what_a_finer_one_separates():
    beat = 60.0 / 90.0
    events = [_note(0.0, 0, duration=beat / 4), _note(beat / 4, 4, duration=beat / 4)]
    fine = log_import.quantize_columns(events, 90.0, "sixteenth")
    coarse = log_import.quantize_columns(events, 90.0, "quarter")
    assert [c.notes for c in fine] == [[(0, 4)], [(4, 4)]]
    assert [c.notes for c in coarse] == [[(0, 4), (4, 4)]]


def test_the_same_log_can_be_requantized_at_another_grid():
    # The point of quantizing at import rather than at capture (#110
    # point 3): the events are never modified, so re-running at another
    # resolution is always available.
    beat = 60.0 / 90.0
    events = [_note(0.0, 0, duration=beat / 4), _note(beat / 4, 4, duration=beat / 4)]
    before = log_import.quantize_columns(events, 90.0, "sixteenth")
    log_import.quantize_columns(events, 90.0, "quarter")
    assert log_import.quantize_columns(events, 90.0, "sixteenth") == before


# --- the editor's own structures -----------------------------------------

def test_score_from_events_builds_real_editor_columns():
    import score_editor_state as ses

    score = log_import.score_from_events([_note(0.0, 0), _note(0.01, 4)], tempo_bpm=120.0,
                                          grid="sixteenth", time_signature=(3, 4), key_fifths=2)
    assert score.tempo_bpm == 120.0
    assert score.time_signature == (3, 4)
    assert score.key_fifths == 2
    assert isinstance(score.columns[0], ses.EditorColumn)
    assert [(n.pitch_class, n.octave) for n in score.columns[0].notes] == [(0, 4), (4, 4)]


def test_an_empty_log_still_opens_as_a_usable_blank_score():
    score = log_import.score_from_events([])
    assert len(score.columns) == 1 and score.columns[0].notes == []


def test_import_log_reads_a_real_file_and_default_path_is_a_musicxml_sibling(tmp_path):
    import session_recorder

    path = str(tmp_path / "session_log_20260101_000000.jsonl")
    recorder = session_recorder.SessionRecorder(path=path)
    recorder.toggle()
    recorder.note_on("z", 0, 4, now=10.0)
    recorder.note_off("z", now=10.5)
    recorder.close(now=10.5)

    score = log_import.import_log(path, grid="sixteenth")
    assert [(n.pitch_class, n.octave) for n in score.columns[0].notes] == [(0, 4)]
    assert log_import.default_score_path(path) == str(tmp_path / "session_log_20260101_000000.musicxml")
