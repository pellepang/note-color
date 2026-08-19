"""Monophonic pitch detection via YIN (de Cheveigne & Kawahara, 2002),
implemented in pure NumPy using FFT-based autocorrelation."""

import numpy as np


def compute_spectrum(window, size=None):
    """rFFT of `window`, zero-padded to `size` (defaults to the next power
    of two >= 2*len(window), matching YIN's autocorrelation padding).
    Shared across the pipeline: YIN, chroma folding, and multipitch
    peak-picking all reuse the one FFT computed here per hop."""
    w = len(window)
    if size is None:
        size = 1
        while size < 2 * w:
            size *= 2
    return np.fft.rfft(window, size)


def _difference_function(x, spectrum, tau_max):
    """d(tau) for tau in [0, tau_max) via FFT-based autocorrelation."""
    w = len(x)
    acf = np.fft.irfft(spectrum * np.conjugate(spectrum))[:tau_max]

    sq = x * x
    cumsum = np.concatenate(([0.0], np.cumsum(sq)))
    tau_range = np.arange(tau_max)
    energy1 = cumsum[w - tau_range] - cumsum[0]
    energy2 = cumsum[w] - cumsum[tau_range]
    return energy1 + energy2 - 2.0 * acf


def _cmndf(d):
    """Cumulative mean normalized difference function."""
    cmnd = np.ones_like(d)
    running_sum = 0.0
    for tau in range(1, len(d)):
        running_sum += d[tau]
        cmnd[tau] = d[tau] * tau / running_sum if running_sum > 0 else 1.0
    return cmnd


def detect_pitch(window, sample_rate, spectrum, fmin=65.0, fmax=1000.0, threshold=0.12):
    """Return (freq_hz, confidence) for the dominant pitch in `window`,
    or (None, 0.0) if no pitch could be confidently detected.
    `spectrum` is `compute_spectrum(window)` (or a caller-shared
    equivalent), reused for FFT-based autocorrelation."""
    x = np.asarray(window, dtype=np.float64)
    w = len(x)

    tau_min = max(1, int(sample_rate / fmax))
    tau_max = min(w - 1, int(sample_rate / fmin))
    if tau_max <= tau_min:
        return None, 0.0

    d = _difference_function(x, spectrum, tau_max + 1)
    cmnd = _cmndf(d)

    tau = None
    t = tau_min
    while t < tau_max:
        if cmnd[t] < threshold:
            while t + 1 < tau_max and cmnd[t + 1] < cmnd[t]:
                t += 1
            tau = t
            break
        t += 1

    if tau is None:
        search = cmnd[tau_min:tau_max]
        if len(search) == 0:
            return None, 0.0
        best = int(np.argmin(search))
        if search[best] >= 0.99:  # nothing periodic in range (silence/noise)
            return None, 0.0
        tau = tau_min + best

    if 0 < tau < len(cmnd) - 1:
        x0, x1, x2 = cmnd[tau - 1], cmnd[tau], cmnd[tau + 1]
        denom = x0 - 2 * x1 + x2
        shift = 0.5 * (x0 - x2) / denom if denom != 0 else 0.0
        tau_refined = tau + shift
    else:
        tau_refined = float(tau)

    if tau_refined <= 0:
        return None, 0.0

    confidence = 1.0 - cmnd[tau]
    freq = sample_rate / tau_refined
    return float(freq), float(max(0.0, min(1.0, confidence)))
