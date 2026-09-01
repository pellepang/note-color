import numpy as np

import config
from detection_backends import (
    SpectralPeakBackend, YinBackend, default_pitch_backend, default_poly_backend,
)
from pitch_detect import compute_spectrum, detect_pitch
from multipitch import detect as multipitch_detect

SAMPLE_RATE = 22050


def make_tone(freq, sample_rate=SAMPLE_RATE, duration=0.1, harmonics=(1.0,)):
    n = int(sample_rate * duration)
    t = np.arange(n) / sample_rate
    signal = np.zeros(n)
    for i, amp in enumerate(harmonics, start=1):
        signal += amp * np.sin(2 * np.pi * freq * i * t)
    return signal.astype(np.float64)


def make_chord(freqs, sample_rate=SAMPLE_RATE, duration=0.4):
    n = int(sample_rate * duration)
    t = np.arange(n) / sample_rate
    signal = np.zeros(n)
    for freq in freqs:
        signal += np.sin(2 * np.pi * freq * t)
    return signal.astype(np.float64)


# --- YinBackend ---------------------------------------------------------

def test_yin_backend_matches_direct_detect_pitch_call():
    tone = make_tone(440.0)
    spectrum = compute_spectrum(tone)
    backend = YinBackend(
        config.FMIN, config.FMAX, config.YIN_THRESHOLD,
        config.YIN_SUBHARMONIC_MAX_MULTIPLE, config.YIN_SUBHARMONIC_MARGIN, config.YIN_SUBHARMONIC_SKIP_CMND,
    )
    expected = detect_pitch(
        tone, SAMPLE_RATE, spectrum, config.FMIN, config.FMAX, config.YIN_THRESHOLD,
        config.YIN_SUBHARMONIC_MAX_MULTIPLE, config.YIN_SUBHARMONIC_MARGIN, config.YIN_SUBHARMONIC_SKIP_CMND,
    )
    assert backend.detect(tone, spectrum, SAMPLE_RATE) == expected


def test_default_pitch_backend_reproduces_direct_call_with_config_defaults():
    tone = make_tone(220.0)
    spectrum = compute_spectrum(tone)
    backend = default_pitch_backend(config)
    expected = detect_pitch(
        tone, SAMPLE_RATE, spectrum, config.FMIN, config.FMAX, config.YIN_THRESHOLD,
        config.YIN_SUBHARMONIC_MAX_MULTIPLE, config.YIN_SUBHARMONIC_MARGIN, config.YIN_SUBHARMONIC_SKIP_CMND,
    )
    assert backend.detect(tone, spectrum, SAMPLE_RATE) == expected


# --- SpectralPeakBackend -------------------------------------------------

def test_spectral_peak_backend_matches_direct_multipitch_detect_call():
    window = make_chord([261.63, 329.63, 392.00])  # C-E-G
    backend = SpectralPeakBackend(
        config.CHORD_MAX_NOTES, config.CHORD_PEAK_MIN_MAG_RATIO, config.CHORD_HARMONIC_TOLERANCE_CENTS,
        config.CHORD_MAX_PEAK_CANDIDATES, config.CHORD_HARMONIC_MAX_NUMBER, config.FMIN, config.FMAX,
    )
    expected = multipitch_detect(
        window, SAMPLE_RATE,
        max_notes=config.CHORD_MAX_NOTES,
        min_mag_ratio=config.CHORD_PEAK_MIN_MAG_RATIO,
        harmonic_tolerance_cents=config.CHORD_HARMONIC_TOLERANCE_CENTS,
        max_peak_candidates=config.CHORD_MAX_PEAK_CANDIDATES,
        harmonic_max_number=config.CHORD_HARMONIC_MAX_NUMBER,
        min_freq_hz=config.FMIN,
        max_freq_hz=config.FMAX,
    )
    assert backend.detect(window, SAMPLE_RATE) == expected


def test_default_poly_backend_reproduces_direct_call_with_config_defaults():
    window = make_chord([261.63, 329.63, 392.00])
    backend = default_poly_backend(config)
    expected = multipitch_detect(
        window, SAMPLE_RATE,
        max_notes=config.CHORD_MAX_NOTES,
        min_mag_ratio=config.CHORD_PEAK_MIN_MAG_RATIO,
        harmonic_tolerance_cents=config.CHORD_HARMONIC_TOLERANCE_CENTS,
        max_peak_candidates=config.CHORD_MAX_PEAK_CANDIDATES,
        harmonic_max_number=config.CHORD_HARMONIC_MAX_NUMBER,
        min_freq_hz=config.FMIN,
        max_freq_hz=config.FMAX,
    )
    assert backend.detect(window, SAMPLE_RATE) == expected
