"""Folds an FFT magnitude spectrum into a 12-bin chroma vector.

Each pitch class's chroma bin sums weighted energy at the 1st/2nd/3rd/4th
harmonics of every octave's candidate fundamental for that pitch class, via
a precomputed Gaussian log-frequency weighting matrix -- not a per-bin
nearest-pitch-class fold. This recovers bass-note discrimination the FFT's
bin width alone can't resolve: at the low end of the range two adjacent
semitones' fundamentals can fall inside the same FFT bin, but their higher
harmonics are spaced far enough apart (in Hz) to be resolved.
"""

import numpy as np

HARMONICS = (1, 2, 3, 4)
HARMONIC_WEIGHTS = {1: 1.0, 2: 0.5, 3: 1.0 / 3, 4: 0.25}
# Empirically checked against a synthesized C-E-G triad (see chord_templates
# matching): 0.5 semitones let each candidate's Gaussian tail pick up
# enough neighboring-pitch-class energy that an unrelated, larger chord
# template could out-score the correct 3-note match on cosine similarity.
# 0.25 keeps candidates well-separated while still resolving adjacent low
# semitones via harmonic summing (see test_chroma.py).
GAUSSIAN_SIGMA_SEMITONES = 0.25
REFERENCE_OCTAVES = range(0, 9)
DEFAULT_BASS_CUTOFF_HZ = 250.0

_matrix_cache = {}


def _weighting_matrix(sample_rate, n_bins):
    key = (sample_rate, n_bins)
    cached = _matrix_cache.get(key)
    if cached is not None:
        return cached

    fft_size = 2 * (n_bins - 1)
    freqs = np.fft.rfftfreq(fft_size, d=1.0 / sample_rate)
    nonzero = freqs > 0
    log_freqs = np.zeros(n_bins)
    log_freqs[nonzero] = np.log2(freqs[nonzero])
    sigma = GAUSSIAN_SIGMA_SEMITONES / 12.0
    nyquist = sample_rate / 2.0

    matrix = np.zeros((12, n_bins))
    for pitch_class in range(12):
        for octave in REFERENCE_OCTAVES:
            midi = (octave + 1) * 12 + pitch_class
            f0 = 440.0 * 2 ** ((midi - 69) / 12.0)
            for harmonic in HARMONICS:
                target = f0 * harmonic
                if target <= 0 or target >= nyquist:
                    continue
                log_target = np.log2(target)
                weight = np.exp(-0.5 * ((log_freqs - log_target) / sigma) ** 2)
                weight[~nonzero] = 0.0
                matrix[pitch_class] += weight * HARMONIC_WEIGHTS[harmonic]

    _matrix_cache[key] = matrix
    return matrix


def fold(spectrum, sample_rate):
    """12-element chroma vector folding all of `spectrum`'s energy."""
    magnitude = np.abs(np.asarray(spectrum))
    matrix = _weighting_matrix(sample_rate, len(magnitude))
    return matrix @ magnitude


def fold_bass(spectrum, sample_rate, cutoff_hz=DEFAULT_BASS_CUTOFF_HZ):
    """12-element chroma vector folding only the low-frequency portion of
    `spectrum` (below `cutoff_hz`) -- its strongest bin is the sounding
    bass note."""
    magnitude = np.abs(np.asarray(spectrum))
    fft_size = 2 * (len(magnitude) - 1)
    freqs = np.fft.rfftfreq(fft_size, d=1.0 / sample_rate)
    masked = np.where(freqs <= cutoff_hz, magnitude, 0.0)
    matrix = _weighting_matrix(sample_rate, len(magnitude))
    return matrix @ masked
