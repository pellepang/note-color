"""Tests for map #123's offline converter seam (issue #129).

Per this repo's convention, the pure shapes and decisions are unit-tested
here; nothing in this file installs, downloads or runs a model. The one
concrete backend that exists so far (`DspNoteTranscriber`) wraps code this
repo already ships, so it is exercised against a synthesized signal rather
than a fixture -- same "synthesize the signal, no binary fixtures" rule
`test_chroma.py`'s `make_tone()` set.
"""

import numpy as np
import pytest

import config
from sound_engine import midi_pitch
from transcribe_backends import (
    BeatGrid,
    ChordSpan,
    ConversionUnavailable,
    DspNoteTranscriber,
    TranscribedNote,
)


def test_conversion_unavailable_carries_the_install_line():
    """#129: the converter refuses rather than degrading, so the message a
    user sees has to be actionable. Same shape as SynthUnavailable (#111)."""
    exc = ConversionUnavailable("Separation needs Demucs.", "pip install -e .[convert]")
    assert exc.install_hint == "pip install -e .[convert]"
    assert "pip install -e .[convert]" in str(exc)
    assert "Separation needs Demucs." in str(exc)


def test_conversion_unavailable_without_a_hint_is_still_a_plain_message():
    exc = ConversionUnavailable("No soundfont found.")
    assert exc.install_hint is None
    assert str(exc) == "No soundfont found."


def test_transcribed_note_duration_is_never_negative():
    """A model emitting an offset before its onset is a model bug, not a
    reason for arithmetic downstream to go negative."""
    assert TranscribedNote(1.0, 2.5, 60).duration_seconds == pytest.approx(1.5)
    assert TranscribedNote(2.0, 1.0, 60).duration_seconds == 0.0


def test_beat_grid_infers_the_modal_beats_per_bar():
    """#130's meter numerator inference in its simplest form: the mode of
    beats-between-downbeats."""
    beats = tuple(i * 0.5 for i in range(17))          # 0.0 .. 8.0
    downbeats = (0.0, 2.0, 4.0, 6.0)                   # every 4 beats
    assert BeatGrid(beats, downbeats).beats_per_bar() == 4


def test_beat_grid_infers_three_four():
    beats = tuple(i * 0.5 for i in range(19))
    downbeats = (0.0, 1.5, 3.0, 4.5, 6.0)              # every 3 beats
    assert BeatGrid(beats, downbeats).beats_per_bar() == 3


def test_beat_grid_takes_the_mode_not_the_first_or_the_mean():
    """One ragged bar must not move the answer -- which is the whole
    reason #130 specifies the mode rather than a division."""
    beats = tuple(i * 0.5 for i in range(21))
    downbeats = (0.0, 2.0, 4.0, 5.5, 7.5, 9.5)         # 4,4,3,4,4
    assert BeatGrid(beats, downbeats).beats_per_bar() == 4


def test_beat_grid_reports_nothing_when_it_cannot_say():
    """Too few downbeats to have an opinion. #130 gates the guess on
    enough observed bars; reporting None is how this half says so."""
    assert BeatGrid((), ()).beats_per_bar() is None
    assert BeatGrid((0.0, 0.5, 1.0), (0.0,)).beats_per_bar() is None
    assert BeatGrid((0.0, 0.5, 1.0), (0.0, 1.0)).beats_per_bar() is None


def test_beat_grid_defaults_are_empty_not_none():
    grid = BeatGrid()
    assert grid.beat_seconds == () and grid.downbeat_seconds == ()


def test_chord_span_is_a_span_not_an_event():
    """#131 puts chord symbols in MusicXML <harmony>, which is span-shaped;
    a lead sheet needs a duration, not an instant."""
    span = ChordSpan(0.0, 2.0, "CΔ7", confidence=0.8)
    assert span.end_seconds > span.start_seconds
    assert span.name == "CΔ7"


def _tone(midi, seconds, sample_rate, amplitude=0.5):
    freq = 440.0 * 2.0 ** ((midi - 69) / 12.0)
    t = np.arange(int(seconds * sample_rate)) / sample_rate
    # A couple of harmonics, so YIN and the multipitch path both have
    # something realistic to lock onto (test_chroma.make_tone()'s approach).
    wave = np.sin(2 * np.pi * freq * t)
    wave += 0.5 * np.sin(2 * np.pi * 2 * freq * t)
    wave += 0.25 * np.sin(2 * np.pi * 3 * freq * t)
    return amplitude * wave / np.max(np.abs(wave))


def test_dsp_backend_returns_seconds_and_midi_not_hops_and_pitch_classes():
    """The conversion this backend exists to perform. batch_transcribe
    speaks onset_hop/duration_hops/pitch_class/octave; every metric #132
    adopts and every neural model #129 selects speaks seconds and MIDI."""
    pytest.importorskip("librosa")
    sample_rate = config.SAMPLE_RATE
    audio = _tone(69, 1.5, sample_rate)  # A4

    notes = DspNoteTranscriber().transcribe(audio, sample_rate)

    assert notes, "expected at least one note from a 1.5s sustained tone"
    for note in notes:
        assert isinstance(note, TranscribedNote)
        assert isinstance(note.pitch_midi, int)
        assert 0 <= note.pitch_midi <= 127
        assert note.onset_seconds >= 0.0
        assert note.offset_seconds >= note.onset_seconds
        # Seconds, not hop indices: a 1.5s clip cannot have an onset at
        # hop 40-odd interpreted as 40 seconds.
        assert note.onset_seconds <= 1.5


def test_dsp_backend_uses_the_repos_own_midi_tuning():
    """Rather than restating the convention -- the drift this seam should
    not introduce."""
    pytest.importorskip("librosa")
    sample_rate = config.SAMPLE_RATE
    notes = DspNoteTranscriber().transcribe(_tone(69, 1.5, sample_rate), sample_rate)
    pitches = {note.pitch_midi for note in notes}
    assert midi_pitch(9, 4) == 69
    assert 69 in pitches or 57 in pitches or 81 in pitches, (
        f"expected A in some octave, got {sorted(pitches)}"
    )


def test_dsp_backend_reports_no_confidence_and_flat_velocity():
    """This pipeline never measured a per-note attack strength, the same
    reason tab_playback.py uses one fixed velocity (#121). Inventing a
    dynamic would be dishonest, and #143 wants a real confidence signal
    from a model that actually has one."""
    pytest.importorskip("librosa")
    sample_rate = config.SAMPLE_RATE
    notes = DspNoteTranscriber().transcribe(_tone(69, 1.5, sample_rate), sample_rate)
    assert notes
    assert all(note.velocity == 1.0 for note in notes)
    assert all(note.confidence is None for note in notes)


def test_dsp_backend_returns_notes_in_time_order():
    pytest.importorskip("librosa")
    sample_rate = config.SAMPLE_RATE
    audio = np.concatenate([_tone(60, 0.6, sample_rate), _tone(67, 0.6, sample_rate)])
    notes = DspNoteTranscriber().transcribe(audio, sample_rate)
    onsets = [note.onset_seconds for note in notes]
    assert onsets == sorted(onsets)


def test_dsp_backend_mono_and_polyphonic_are_a_selection_not_a_mode():
    """Both lists are always computed by batch_transcribe.transcribe();
    this backend picks one. Asserting both run keeps that documented
    behaviour honest."""
    pytest.importorskip("librosa")
    sample_rate = config.SAMPLE_RATE
    audio = _tone(60, 1.2, sample_rate)
    poly = DspNoteTranscriber(polyphonic=True).transcribe(audio, sample_rate)
    mono = DspNoteTranscriber(polyphonic=False).transcribe(audio, sample_rate)
    assert isinstance(poly, list) and isinstance(mono, list)
