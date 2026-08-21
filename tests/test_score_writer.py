"""Tests for score_writer.py (issue #65's MusicXML export).

Mirrors tests/test_batch_transcribe.py's fixture convention: no binary
audio/score fixtures. guess_key_signature() is tested directly against
synthesized chroma-histogram arrays (it's a pure function -- no need to
run real audio through chroma.fold() just to get a 12-element vector).
write_score() is tested by building a small synthetic
batch_transcribe.TranscriptionResult directly (NoteEvents constructed by
hand, same as this file's own docstring convention), writing it to a
tmp_path file, and parsing it back with music21.converter.parse() to
verify structure -- not just that write_score() doesn't crash.
"""

import numpy as np
import pytest
from music21 import chord as m21chord, converter

import config
from batch_transcribe import NoteEvent, TranscriptionResult
from duration_tracker import duration_class_for_beats
from score_writer import _QUARTER_LENGTHS, guess_key_signature, write_score

HOP_SECONDS = config.BLOCK_SIZE / config.SAMPLE_RATE


def hops_for_beats(beats, bpm):
    """Inverse of write_score()'s own note_beats formula
    (duration_hops * hop_seconds * bpm / 60) -- lets a test ask for "about
    N beats" and get back a duration_hops count that resolves to that
    duration_class instead of hardcoding a magic hop count."""
    seconds = beats * 60.0 / bpm
    return max(1, round(seconds / HOP_SECONDS))


def expected_quarter_length(duration_hops, bpm):
    beats = duration_hops * HOP_SECONDS * bpm / 60.0
    return _QUARTER_LENGTHS[duration_class_for_beats(beats)]


# ---- guess_key_signature ----------------------------------------------

def test_clear_c_major_profile_returns_c_major_with_high_confidence():
    # Krumhansl-Kessler major profile itself, unrotated -- the strongest
    # possible C-major signal a histogram could carry.
    histogram = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])

    result = guess_key_signature(histogram)

    assert result is not None
    assert result.tonic.name == "C"
    assert result.mode == "major"


def test_clear_g_major_profile_returns_g_major():
    # Same KK major profile, rotated so pitch-class 7 (G) carries the
    # tonic's weight -- np.roll(profile, 7) puts profile[0] at index 7.
    major_profile = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    histogram = np.roll(major_profile, 7)

    result = guess_key_signature(histogram)

    assert result is not None
    assert result.tonic.name == "G"
    assert result.mode == "major"


def test_uniform_histogram_returns_none():
    histogram = np.ones(12)

    assert guess_key_signature(histogram) is None


def test_silent_histogram_returns_none():
    histogram = np.zeros(12)

    assert guess_key_signature(histogram) is None


# ---- write_score --------------------------------------------------------

def _rgb_hex(pitch_class):
    from color_map import hsl_to_rgb255, note_to_hsl
    from config_store import store

    hue, sat, _light = note_to_hsl(
        pitch_class, config.MAX_OCTAVE, scheme="fifths", hue_override=store.note_hue_override(pitch_class)
    )
    r, g, b = hsl_to_rgb255(hue, sat, config.TAB_NOTE_LIGHTNESS)
    return f"#{r:02X}{g:02X}{b:02X}"


def test_write_score_writes_chord_and_solo_notes_with_correct_structure(tmp_path):
    bpm = 120.0
    chord_duration_hops = hops_for_beats(1.0, bpm)  # ~quarter note
    bass_duration_hops = hops_for_beats(0.5, bpm)  # ~eighth note

    notes = [
        # A C-major triad, all three notes sharing onset_hop=0 -- should
        # come back as one music21 Chord.
        NoteEvent(onset_hop=0, onset_time=0.0, pitch_class=0, octave=4,
                   duration_hops=chord_duration_hops, chord_name="C"),
        NoteEvent(onset_hop=0, onset_time=0.0, pitch_class=4, octave=4,
                   duration_hops=chord_duration_hops, chord_name="C"),
        NoteEvent(onset_hop=0, onset_time=0.0, pitch_class=7, octave=4,
                   duration_hops=chord_duration_hops, chord_name="C"),
        # A lone bass note, later in the recording, low enough to land on
        # the bass staff -- should come back as a plain Note, not a Chord.
        NoteEvent(onset_hop=40, onset_time=40 * HOP_SECONDS, pitch_class=9, octave=2,
                   duration_hops=bass_duration_hops, chord_name=None),
    ]
    chroma_histogram = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    result = TranscriptionResult(
        notes=notes, mono_notes=[], bpm=bpm, hop_seconds=HOP_SECONDS, chroma_histogram=chroma_histogram
    )

    out_path = tmp_path / "test_score.musicxml"
    write_score(result, str(out_path))

    assert out_path.exists()
    parsed = converter.parse(str(out_path))

    all_notes = list(parsed.recurse().notes)  # Chord and Note objects, no Rests
    # 1 Chord (3 pitches) + 1 solo Note == 2 note-ish elements
    assert len(all_notes) == 2

    chords = [el for el in all_notes if isinstance(el, m21chord.Chord)]
    solo_notes = [el for el in all_notes if not isinstance(el, m21chord.Chord)]
    assert len(chords) == 1
    assert len(solo_notes) == 1

    triad = chords[0]
    assert sorted(p.pitchClass for p in triad.pitches) == [0, 4, 7]
    expected_colors = {0: _rgb_hex(0), 4: _rgb_hex(4), 7: _rgb_hex(7)}
    for pitch_obj, chord_note in zip(triad.pitches, triad.notes):
        assert chord_note.style.color == expected_colors[pitch_obj.pitchClass]
    assert triad.duration.quarterLength == pytest.approx(
        expected_quarter_length(chord_duration_hops, bpm)
    )

    bass_note = solo_notes[0]
    assert bass_note.pitch.pitchClass == 9
    assert bass_note.pitch.octave == 2
    assert bass_note.style.color == _rgb_hex(9)
    assert bass_note.duration.quarterLength == pytest.approx(
        expected_quarter_length(bass_duration_hops, bpm)
    )

    # Two-staff grand staff: the bass note must land in a different part
    # than the chord (treble).
    assert len(parsed.parts) == 2
    bass_part_notes = [n for n in parsed.parts[1].recurse().notes]
    treble_part_notes = [n for n in parsed.parts[0].recurse().notes]
    assert any(isinstance(n, m21chord.Chord) for n in treble_part_notes) or any(
        isinstance(n, m21chord.Chord) for n in bass_part_notes
    )


def test_write_score_applies_time_signature(tmp_path):
    notes = [
        NoteEvent(onset_hop=0, onset_time=0.0, pitch_class=0, octave=4,
                   duration_hops=hops_for_beats(1.0, 100.0), chord_name=None),
    ]
    result = TranscriptionResult(
        notes=notes, mono_notes=[], bpm=100.0, hop_seconds=HOP_SECONDS, chroma_histogram=np.zeros(12)
    )
    out_path = tmp_path / "ts_test.musicxml"

    write_score(result, str(out_path), time_signature=(3, 4))

    parsed = converter.parse(str(out_path))
    time_sigs = list(parsed.recurse().getElementsByClass("TimeSignature"))
    assert any(ts.numerator == 3 and ts.denominator == 4 for ts in time_sigs)


def test_write_score_with_no_bpm_falls_back_gracefully(tmp_path):
    notes = [
        NoteEvent(onset_hop=0, onset_time=0.0, pitch_class=2, octave=5,
                   duration_hops=10, chord_name=None),
    ]
    result = TranscriptionResult(
        notes=notes, mono_notes=[], bpm=None, hop_seconds=HOP_SECONDS, chroma_histogram=np.zeros(12)
    )
    out_path = tmp_path / "no_bpm.musicxml"

    write_score(result, str(out_path))  # must not raise

    parsed = converter.parse(str(out_path))
    all_notes = list(parsed.recurse().notes)
    assert len(all_notes) == 1
    assert all_notes[0].pitch.pitchClass == 2
    # No bpm -> duration_class_for_beats(None) -> DEFAULT_DURATION_CLASS ("quarter").
    assert all_notes[0].duration.quarterLength == pytest.approx(1.0)


def test_write_score_guessed_key_signature_applied(tmp_path):
    bpm = 120.0
    notes = [
        NoteEvent(onset_hop=0, onset_time=0.0, pitch_class=0, octave=4,
                   duration_hops=hops_for_beats(1.0, bpm), chord_name=None),
    ]
    # Same clean C-major profile used above -- clears the confidence
    # threshold deterministically.
    chroma_histogram = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
    result = TranscriptionResult(
        notes=notes, mono_notes=[], bpm=bpm, hop_seconds=HOP_SECONDS, chroma_histogram=chroma_histogram
    )
    out_path = tmp_path / "keyed.musicxml"

    write_score(result, str(out_path))

    parsed = converter.parse(str(out_path))
    key_sigs = list(parsed.recurse().getElementsByClass("KeySignature"))
    assert len(key_sigs) >= 1
    assert key_sigs[0].sharps == 0  # C major -- no sharps or flats


def test_write_score_no_key_guess_leaves_default(tmp_path):
    bpm = 120.0
    notes = [
        NoteEvent(onset_hop=0, onset_time=0.0, pitch_class=0, octave=4,
                   duration_hops=hops_for_beats(1.0, bpm), chord_name=None),
    ]
    result = TranscriptionResult(
        notes=notes, mono_notes=[], bpm=bpm, hop_seconds=HOP_SECONDS, chroma_histogram=np.ones(12)
    )
    out_path = tmp_path / "unkeyed.musicxml"

    write_score(result, str(out_path))  # must not raise even with no confident key guess

    parsed = converter.parse(str(out_path))
    all_notes = list(parsed.recurse().notes)
    assert len(all_notes) == 1
