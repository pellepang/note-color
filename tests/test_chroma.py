import numpy as np
import pytest

from chroma import fold, fold_bass
from pitch_detect import compute_spectrum

SAMPLE_RATE = 22050
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def freq_for(pitch_class, octave):
    midi = (octave + 1) * 12 + pitch_class
    return 440.0 * 2 ** ((midi - 69) / 12)


def make_tone(freq, sample_rate=SAMPLE_RATE, duration=0.4, harmonics=(1.0,)):
    n = int(sample_rate * duration)
    t = np.arange(n) / sample_rate
    signal = np.zeros(n)
    for i, amp in enumerate(harmonics, start=1):
        signal += amp * np.sin(2 * np.pi * freq * i * t)
    return signal.astype(np.float64)


@pytest.mark.parametrize("pitch_class,octave", [(0, 4), (4, 4), (9, 4), (7, 5)])
def test_pure_tone_energy_concentrated_in_correct_pitch_class(pitch_class, octave):
    tone = make_tone(freq_for(pitch_class, octave))
    chroma = fold(compute_spectrum(tone), SAMPLE_RATE)
    assert len(chroma) == 12
    assert int(np.argmax(chroma)) == pitch_class


def test_adjacent_low_semitones_are_discriminated():
    # C2 (65.41Hz) and C#2 (69.30Hz) are less than one FFT bin apart at
    # SAMPLE_RATE=22050 (bin width ~5.38Hz at the 4096-point padded FFT) --
    # only harmonic summing (not the fundamental bin alone) can tell them
    # apart, per the chroma design rationale.
    c2 = make_tone(freq_for(0, 2), harmonics=(1.0, 0.6, 0.4, 0.3))
    cs2 = make_tone(freq_for(1, 2), harmonics=(1.0, 0.6, 0.4, 0.3))

    chroma_c2 = fold(compute_spectrum(c2), SAMPLE_RATE)
    chroma_cs2 = fold(compute_spectrum(cs2), SAMPLE_RATE)

    assert int(np.argmax(chroma_c2)) == 0
    assert int(np.argmax(chroma_cs2)) == 1


def test_fold_bass_isolates_lower_note_from_simultaneous_higher_note():
    bass = make_tone(freq_for(0, 2), harmonics=(1.0, 0.6, 0.4, 0.3))  # C2
    treble = make_tone(freq_for(4, 5), harmonics=(1.0, 0.6, 0.4, 0.3))  # E5, well above 250Hz
    mixed_spectrum = compute_spectrum(bass) + compute_spectrum(treble)

    bass_chroma = fold_bass(mixed_spectrum, SAMPLE_RATE)

    assert int(np.argmax(bass_chroma)) == 0
    assert bass_chroma[0] > 5 * bass_chroma[4]


def test_fold_bass_suppresses_energy_above_cutoff():
    # No window function is applied anywhere in this pipeline (matches
    # pitch_detect.py), so a pure tone's rectangular-window spectral
    # leakage does reach low bins -- fold_bass can't be literally zero,
    # but it should suppress a well-above-cutoff tone to a small fraction
    # of its full-band energy.
    treble = make_tone(freq_for(9, 4))  # A4, 440Hz
    spectrum = compute_spectrum(treble)

    full_chroma = fold(spectrum, SAMPLE_RATE)
    bass_chroma = fold_bass(spectrum, SAMPLE_RATE)

    assert np.max(bass_chroma) < 0.05 * np.max(full_chroma)
