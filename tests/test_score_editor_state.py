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

import pytest

import config
import score_editor_state
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


def test_load_score_refuses_a_multi_track_file(tmp_path):
    """Issue #131: before this guard, load_score() iterated parsed.parts
    with no count check, so a four-part file merged by offset into
    four-note chords and save_score() then wrote it back as two staves --
    silent data loss, reachable by opening any multi-part MusicXML."""
    from music21 import note as m21note
    from music21 import stream as m21stream

    score = m21stream.Score()
    for i, pitch_name in enumerate(("C4", "E4", "G4", "B4")):
        part = m21stream.Part()
        part.partName = f"Track {i + 1}"
        part.append(m21note.Note(pitch_name, quarterLength=1.0))
        score.insert(0, part)
    path = tmp_path / "four_tracks.musicxml"
    score.write("musicxml", fp=str(path))

    with pytest.raises(score_editor_state.MultiTrackScoreError) as excinfo:
        score_editor_state.load_score(path)
    assert "4 parts" in str(excinfo.value)


def test_load_score_refuses_two_unbraced_parts(tmp_path):
    """Two parts are one grand-staff track only when a brace StaffGroup
    joins them; two independent single-staff tracks are a multi-track file
    and must be refused just like four are."""
    from music21 import note as m21note
    from music21 import stream as m21stream

    score = m21stream.Score()
    for i, pitch_name in enumerate(("C4", "G3")):
        part = m21stream.Part()
        part.partName = f"Track {i + 1}"
        part.append(m21note.Note(pitch_name, quarterLength=1.0))
        score.insert(0, part)
    path = tmp_path / "two_tracks.musicxml"
    score.write("musicxml", fp=str(path))

    with pytest.raises(score_editor_state.MultiTrackScoreError):
        score_editor_state.load_score(path)


def test_load_score_still_accepts_its_own_grand_staff(tmp_path):
    """The guard must not refuse the two-PartStaff grand staff save_score()
    itself writes -- the round trip that every other test here relies on.
    Regression cover for the guard being written against the PartStaff
    class (which music21 does not preserve) rather than the brace."""
    score = score_editor_state.new_blank_score()
    score.columns[0].notes = [
        score_editor_state.EditorNote(pitch_class=0, octave=4),
        score_editor_state.EditorNote(pitch_class=7, octave=2),
    ]
    path = tmp_path / "grand_staff.musicxml"
    score_editor_state.save_score(score, path)

    loaded = score_editor_state.load_score(path)
    assert len(loaded.columns) == 1
    assert {(n.pitch_class, n.octave) for n in loaded.columns[0].notes} == {(0, 4), (7, 2)}


# --- Tuplets (map #123, issue #130) ---------------------------------------


def test_triplet_column_round_trips_through_save_and_load(tmp_path):
    """The point of issue #130's tuplet work: a triplet written to
    MusicXML has to come back as the same duration_class, not as the
    dotted-sixteenth its quarterLength would otherwise snap to.

    Three triplet-eighths are used deliberately -- they sum to exactly one
    beat, so the score stays measure-aligned and music21's own
    tie-splitting can't confound the assertion (same construction rule the
    other round-trip tests in this file follow)."""
    score = score_editor_state.new_blank_score()
    score.columns = [
        EditorColumn(notes=[EditorNote(pitch_class=pc, octave=4)], duration_class="triplet-eighth")
        for pc in (0, 4, 7)
    ]
    path = tmp_path / "triplets.musicxml"
    save_score(score, path)

    loaded = load_score(path)
    assert [c.duration_class for c in loaded.columns] == ["triplet-eighth"] * 3
    assert [c.notes[0].pitch_class for c in loaded.columns] == [0, 4, 7]


def test_saved_triplet_carries_a_real_music21_tuplet(tmp_path):
    """A fractional quarterLength alone is not a triplet -- correct
    notation needs a Tuplet, which is what makes MusicXML emit
    <time-modification>. music21 builds it from the exact Fraction in
    QUARTER_LENGTHS, so this asserts that actually happened rather than
    trusting the mechanism."""
    from music21 import converter

    score = score_editor_state.new_blank_score()
    score.columns = [
        EditorColumn(notes=[EditorNote(pitch_class=0, octave=4)], duration_class="triplet-eighth")
        for _ in range(3)
    ]
    path = tmp_path / "tuplet_marks.musicxml"
    save_score(score, path)

    parsed = converter.parse(str(path))
    sounding = [n for n in parsed.recurse().notes]
    assert sounding, "expected at least one sounding note"
    for element in sounding:
        assert element.duration.tuplets, "triplet lost its Tuplet on the way to MusicXML"
        assert element.duration.tuplets[0].numberNotesActual == 3

    assert "<time-modification>" in path.read_text(encoding="utf-8")


def test_triplet_offsets_survive_the_offset_quantizer(tmp_path):
    """The hazard the divisor change guards: a triplet-eighth onset falls
    at 1/3 of a quarter, and the previous 32nd-note-only grid (divisor 8)
    would have shifted it to 3/8. Asserts the second and third onsets are
    exact thirds, not eighths."""
    from fractions import Fraction
    from music21 import converter

    score = score_editor_state.new_blank_score()
    score.columns = [
        EditorColumn(notes=[EditorNote(pitch_class=0, octave=4)], duration_class="triplet-eighth")
        for _ in range(3)
    ]
    path = tmp_path / "triplet_offsets.musicxml"
    save_score(score, path)

    parsed = converter.parse(str(path))
    treble = parsed.parts[0]
    offsets = sorted({n.getOffsetInHierarchy(parsed) for n in treble.recurse().notes})
    assert offsets[:3] == [Fraction(0), Fraction(1, 3), Fraction(2, 3)]


def test_plain_durations_are_unaffected_by_the_widened_divisors(tmp_path):
    """Adding divisor 12 must not perturb ordinary dyadic scores -- the
    regression this change most plausibly causes."""
    score = score_editor_state.new_blank_score()
    score.columns = [
        EditorColumn(notes=[EditorNote(pitch_class=0, octave=4)], duration_class="quarter"),
        EditorColumn(notes=[EditorNote(pitch_class=4, octave=4)], duration_class="dotted-eighth"),
        EditorColumn(notes=[EditorNote(pitch_class=7, octave=4)], duration_class="sixteenth"),
        EditorColumn(notes=[EditorNote(pitch_class=9, octave=4)], duration_class="half"),
    ]
    path = tmp_path / "plain.musicxml"
    save_score(score, path)

    loaded = load_score(path)
    assert [c.duration_class for c in loaded.columns] == [
        "quarter", "dotted-eighth", "sixteenth", "half",
    ]
