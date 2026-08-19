import numpy as np

from multipitch import detect

SAMPLE_RATE = 22050


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


def as_note_set(notes):
    return {(n.pitch_class, n.octave) for n in notes}


def test_single_pure_tone_detected_as_one_note():
    tone = make_tone(freq_for(9, 4))  # A4
    notes = detect(tone, SAMPLE_RATE)
    assert as_note_set(notes) == {(9, 4)}


def test_major_triad_notes_all_detected():
    c = make_tone(freq_for(0, 4))
    e = make_tone(freq_for(4, 4))
    g = make_tone(freq_for(7, 4))
    mixed = c + e + g
    notes = detect(mixed, SAMPLE_RATE)
    assert as_note_set(notes) == {(0, 4), (4, 4), (7, 4)}


def test_own_harmonics_not_double_counted_as_separate_notes():
    # A single instrument's overtones (2nd = octave up, 3rd = octave+fifth
    # up) must collapse into its one fundamental, not read as a 3-note chord.
    tone = make_tone(freq_for(0, 3), harmonics=(1.0, 0.6, 0.4))  # C3 + overtones
    notes = detect(tone, SAMPLE_RATE)
    assert as_note_set(notes) == {(0, 3)}


def test_detected_notes_are_capped_at_max_notes():
    pitch_classes = [0, 2, 4, 5, 7, 9, 11, 1]  # 8 distinct notes, one octave
    tones = sum(make_tone(freq_for(pc, 4)) for pc in pitch_classes)
    notes = detect(tones, SAMPLE_RATE, max_notes=6)
    assert len(notes) <= 6


def test_each_note_carries_a_confidence_between_zero_and_one():
    tone = make_tone(freq_for(0, 4))
    notes = detect(tone, SAMPLE_RATE)
    assert len(notes) == 1
    assert 0.0 < notes[0].confidence <= 1.0


def test_silence_detects_no_notes():
    silence = np.zeros(2048)
    notes = detect(silence, SAMPLE_RATE)
    assert notes == []
