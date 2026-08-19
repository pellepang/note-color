import numpy as np
import pytest

from pitch_detect import compute_spectrum, detect_pitch

SAMPLE_RATE = 22050


def test_compute_spectrum_matches_manual_rfft_at_given_size():
    window = np.arange(2048, dtype=np.float64)
    spectrum = compute_spectrum(window, size=4096)
    expected = np.fft.rfft(window, 4096)
    assert np.allclose(spectrum, expected)


def test_compute_spectrum_defaults_to_next_pow2_of_double_length():
    window = np.arange(2048, dtype=np.float64)
    spectrum = compute_spectrum(window)
    assert len(spectrum) == 4096 // 2 + 1


def make_tone(freq, sample_rate=SAMPLE_RATE, duration=0.1, harmonics=(1.0,)):
    n = int(sample_rate * duration)
    t = np.arange(n) / sample_rate
    signal = np.zeros(n)
    for i, amp in enumerate(harmonics, start=1):
        signal += amp * np.sin(2 * np.pi * freq * i * t)
    return signal.astype(np.float64)


@pytest.mark.parametrize("freq", [110.0, 220.0, 440.0, 880.0])
def test_pure_tone_detected_within_10_cents(freq):
    tone = make_tone(freq)
    detected, confidence = detect_pitch(tone, SAMPLE_RATE, compute_spectrum(tone))
    assert detected is not None
    cents_off = 1200 * np.log2(detected / freq)
    assert abs(cents_off) < 10
    assert confidence > 0.5


def test_tone_with_harmonics_detected():
    tone = make_tone(220.0, harmonics=(1.0, 0.5, 0.25))
    detected, confidence = detect_pitch(tone, SAMPLE_RATE, compute_spectrum(tone))
    assert detected is not None
    cents_off = 1200 * np.log2(detected / 220.0)
    assert abs(cents_off) < 10


def test_silence_returns_none():
    silence = np.zeros(2048)
    detected, confidence = detect_pitch(silence, SAMPLE_RATE, compute_spectrum(silence))
    assert detected is None


def test_white_noise_low_confidence_or_none():
    rng = np.random.default_rng(0)
    noise = rng.normal(0, 1, 2048)
    detected, confidence = detect_pitch(noise, SAMPLE_RATE, compute_spectrum(noise))
    assert detected is None or confidence < 0.5
