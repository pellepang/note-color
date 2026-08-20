"""Tests for batch_transcribe.py (issue #55's offline transcription path).

Synthesizes test signals directly as in-memory NumPy arrays (this repo's
existing convention -- see tests/test_chroma.py's make_tone()) rather than
shipping binary fixtures, and calls transcribe() straight on the array
without going through load_audio()/a real file for the tests that don't
need it."""

import numpy as np

import config
from batch_transcribe import transcribe


SAMPLE_RATE = config.SAMPLE_RATE


def freq_for(pitch_class, octave):
    midi = (octave + 1) * 12 + pitch_class
    return 440.0 * 2 ** ((midi - 69) / 12)


def make_tone(freq, sample_rate=SAMPLE_RATE, duration=1.0, harmonics=(1.0, 0.5, 0.3)):
    n = int(sample_rate * duration)
    t = np.arange(n) / sample_rate
    signal = np.zeros(n)
    for i, amp in enumerate(harmonics, start=1):
        signal += amp * np.sin(2 * np.pi * freq * i * t)
    return signal.astype(np.float64)


def make_click_track(bpm, sample_rate=SAMPLE_RATE, duration=4.0):
    """A clear periodic pulse -- short bursts of noise at a fixed bpm --
    for exercising the tempo estimate."""
    n = int(sample_rate * duration)
    signal = np.zeros(n)
    beat_interval = 60.0 / bpm
    click_len = int(0.02 * sample_rate)
    rng = np.random.default_rng(0)
    t = 0.0
    while t < duration:
        start = int(t * sample_rate)
        end = min(start + click_len, n)
        signal[start:end] += rng.standard_normal(end - start)
        t += beat_interval
    return signal.astype(np.float64)


def test_sustained_tone_produces_one_note_with_roughly_matching_duration():
    duration_s = 1.0
    tone = make_tone(freq_for(9, 4), duration=duration_s)  # A4, sustained

    result = transcribe(tone, SAMPLE_RATE)

    assert len(result.notes) >= 1
    note = result.notes[0]
    assert note.pitch_class == 9
    assert note.octave == 4

    hop_seconds = config.BLOCK_SIZE / SAMPLE_RATE
    measured_duration_s = note.duration_hops * hop_seconds
    # A generous tolerance -- decay-ratio threshold crossing, onset
    # detection latency, and the analysis window's own ~93ms smear all
    # eat into "exactly duration_s", so this just checks the right order
    # of magnitude, not an exact match.
    assert measured_duration_s > duration_s * 0.4


def test_transcribe_returns_tempo_estimate_for_a_clear_periodic_pulse():
    clicks = make_click_track(120.0, duration=6.0)

    result = transcribe(clicks, SAMPLE_RATE)

    assert result.bpm is not None
    assert result.bpm > 0
    # Tempo estimators (this one included) commonly lock onto a half/double
    # -time multiple of the true tempo rather than the literal bpm -- allow
    # 120, 60, or 240 rather than asserting an exact 120.0 match.
    ratio = result.bpm / 120.0
    assert any(abs(ratio - mult) < 0.15 for mult in (0.5, 1.0, 2.0))


def test_transcribe_empty_audio_returns_no_notes_and_no_tempo():
    result = transcribe(np.zeros(0), SAMPLE_RATE)
    assert result.notes == []
    assert result.mono_notes == []
    assert result.bpm is None


def test_transcribe_short_audio_below_one_hop_is_handled_gracefully():
    tiny = np.zeros(config.BLOCK_SIZE // 2)
    result = transcribe(tiny, SAMPLE_RATE)
    assert result.notes == []
