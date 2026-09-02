"""Tests for score_editor_state.py (issue #98's data-layer half).

Mirrors tests/test_score_writer.py's/test_batch_transcribe.py's fixture
convention: no binary score fixtures, everything built by hand and
round-tripped through tmp_path. save_score()/load_score() round-trip
tests deliberately keep each fixture's total duration aligned to a whole
number of measures (see save_score()'s and load_score()'s own docstrings)
-- a score whose total duration straddles a barline gets tie-split by
music21's own makeMeasures() during write, which is a real, documented
(not this module's bug) MusicXML-format limitation, not something a
data-layer round-trip test should be exercising.
"""

import config
from batch_transcribe import NoteEvent, TranscriptionResult
from duration_tracker import DEFAULT_DURATION_CLASS
from score_editor_state import (
    DEFAULT_TEMPO_BPM,
    EditHistory,
    EditorColumn,
    EditorNote,
    EditorScore,
    load_score,
    new_blank_score,
    save_score,
)
from score_writer import write_score

import numpy as np


# ---- new_blank_score ----------------------------------------------------

def test_new_blank_score_defaults():
    score = new_blank_score()

    assert score.time_signature == (4, 4)
    assert score.key_fifths == 0
    assert score.tempo_bpm == 90.0
    assert len(score.columns) == 1
    assert score.columns[0].notes == []
    assert score.columns[0].duration_class == DEFAULT_DURATION_CLASS


# ---- save_score / load_score round trip ----------------------------------

def _measure_aligned_score():
    """A (3, 4)-time score whose 5 columns sum to exactly two full
    measures (3 + 3 beats): a chord column, a rest column, and a solo
    note column in the first measure; a half note + quarter note in the
    second. Non-default key/tempo too, so every EditorScore field gets
    exercised by the round trip."""
    return EditorScore(
        time_signature=(3, 4),
        key_fifths=2,
        tempo_bpm=132.0,
        columns=[
            EditorColumn(
                notes=[
                    EditorNote(pitch_class=0, octave=4),
                    EditorNote(pitch_class=4, octave=4),
                    EditorNote(pitch_class=7, octave=4),
                ],
                duration_class="quarter",
            ),
            EditorColumn(notes=[], duration_class="quarter"),  # Rest
            EditorColumn(notes=[EditorNote(pitch_class=9, octave=2)], duration_class="quarter"),
            EditorColumn(notes=[EditorNote(pitch_class=2, octave=5)], duration_class="half"),
            EditorColumn(notes=[EditorNote(pitch_class=6, octave=3)], duration_class="quarter"),
        ],
    )


def test_save_load_round_trip_preserves_everything(tmp_path):
    score = _measure_aligned_score()
    path = tmp_path / "roundtrip.musicxml"

    save_score(score, str(path))
    loaded = load_score(str(path))

    assert loaded == score


def test_save_load_round_trip_chord_column_notes_unordered_set(tmp_path):
    # Chord-member ordering isn't musically meaningful -- confirm the
    # *set* of notes survives even if music21 reorders pitches within a
    # Chord internally (it sorts by pitch, so this is a real possibility).
    score = _measure_aligned_score()
    path = tmp_path / "chord.musicxml"

    save_score(score, str(path))
    loaded = load_score(str(path))

    original_chord = {(n.pitch_class, n.octave) for n in score.columns[0].notes}
    loaded_chord = {(n.pitch_class, n.octave) for n in loaded.columns[0].notes}
    assert original_chord == loaded_chord


def test_save_load_round_trip_rest_column_is_empty(tmp_path):
    score = _measure_aligned_score()
    path = tmp_path / "rest.musicxml"

    save_score(score, str(path))
    loaded = load_score(str(path))

    assert loaded.columns[1].notes == []


# ---- load_score defaults --------------------------------------------------

def test_load_score_defaults_tempo_when_no_metronome_mark(tmp_path):
    # score_writer.write_score() never writes a tempo marking -- loading
    # one of its own outputs must default to 90.0, not crash or guess.
    hop_seconds = config.BLOCK_SIZE / config.SAMPLE_RATE
    bpm = 120.0
    notes = [
        NoteEvent(
            onset_hop=0, onset_time=0.0, pitch_class=0, octave=4,
            duration_hops=max(1, round(60.0 / bpm / hop_seconds)), chord_name=None,
        ),
    ]
    result = TranscriptionResult(
        notes=notes, mono_notes=[], bpm=bpm, hop_seconds=hop_seconds, chroma_histogram=np.zeros(12)
    )
    path = tmp_path / "no_tempo.musicxml"
    write_score(result, str(path))

    loaded = load_score(str(path))

    assert loaded.tempo_bpm == DEFAULT_TEMPO_BPM
    assert loaded.tempo_bpm == 90.0


def test_load_score_defaults_key_fifths_when_no_key_signature(tmp_path):
    # write_score() only writes a key signature when guess_key_signature()
    # is confident -- a flat/uniform histogram means no KeySignature
    # element at all, which should default to 0 sharps, not crash.
    hop_seconds = config.BLOCK_SIZE / config.SAMPLE_RATE
    notes = [
        NoteEvent(onset_hop=0, onset_time=0.0, pitch_class=2, octave=5, duration_hops=10, chord_name=None),
    ]
    result = TranscriptionResult(
        notes=notes, mono_notes=[], bpm=100.0, hop_seconds=hop_seconds, chroma_histogram=np.ones(12)
    )
    path = tmp_path / "no_key.musicxml"
    write_score(result, str(path))

    loaded = load_score(str(path))

    assert loaded.key_fifths == 0


# ---- EditHistory ----------------------------------------------------------

def _score_with_marker(marker):
    """A trivial distinguishable EditorScore for EditHistory tests --
    only tempo_bpm varies, cheap to compare by identity of that field."""
    return EditorScore(time_signature=(4, 4), key_fifths=0, tempo_bpm=float(marker), columns=[])


def test_edit_history_undo_redo_basic_cycle():
    history = EditHistory()
    v0 = _score_with_marker(0)
    v1 = _score_with_marker(1)

    history.record(v0)
    current = v1

    undone = history.undo(current)
    assert undone.tempo_bpm == 0

    redone = history.redo(undone)
    assert redone.tempo_bpm == 1


def test_edit_history_undo_at_start_returns_none():
    history = EditHistory()

    assert history.undo(_score_with_marker(0)) is None


def test_edit_history_redo_at_end_returns_none():
    history = EditHistory()
    history.record(_score_with_marker(0))
    current = _score_with_marker(1)
    history.undo(current)

    # redo() once puts us back at "current" == marker 1; a second redo()
    # has nothing left to redo.
    history.redo(_score_with_marker(0))
    assert history.redo(_score_with_marker(1)) is None


def test_edit_history_new_edit_after_undo_clears_redo_stack():
    history = EditHistory()
    history.record(_score_with_marker(0))
    current = _score_with_marker(1)

    undone = history.undo(current)  # redo_stack now has marker(1)
    assert undone.tempo_bpm == 0

    # A fresh edit + record() after the undo should clear the redo stack.
    history.record(undone)
    assert history.redo(_score_with_marker(2)) is None


def test_edit_history_bounded_depth_drops_oldest():
    history = EditHistory()
    max_depth = config.EDITOR_UNDO_MAX_DEPTH

    current = _score_with_marker(0)
    for i in range(1, max_depth + 10):
        history.record(_score_with_marker(i - 1))
        current = _score_with_marker(i)

    assert len(history.undo_stack) == max_depth

    # Undo repeatedly; the oldest surviving snapshot should be marker
    # (max_depth + 10 - 1 - max_depth) == 9, not marker 0 (dropped).
    for _ in range(max_depth):
        current = history.undo(current)
        assert current is not None

    assert history.undo(current) is None
    assert current.tempo_bpm == 9.0
