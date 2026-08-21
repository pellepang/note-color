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


def _parabolic_vertex(cmnd, t):
    """Sub-sample offset and estimated value of the local minimum at grid
    index `t`, via 3-point parabolic interpolation. Falls back to the raw
    grid value (zero offset) if the neighbors don't form a proper local
    minimum (a non-positive denominator -- e.g. `t` at the very edge of
    the searched range)."""
    if 0 < t < len(cmnd) - 1:
        x0, x1, x2 = cmnd[t - 1], cmnd[t], cmnd[t + 1]
        denom = x0 - 2 * x1 + x2
        if denom > 0:
            shift = 0.5 * (x0 - x2) / denom
            value = x1 - (x0 - x2) ** 2 / (8 * denom)
            return shift, max(0.0, value)
    return 0.0, cmnd[t]


def detect_pitch(
    window,
    sample_rate,
    spectrum,
    fmin=65.0,
    fmax=1000.0,
    threshold=0.12,
    subharmonic_max_multiple=4,
    subharmonic_margin=0.5,
    subharmonic_skip_cmnd=0.01,
):
    """Return (freq_hz, confidence) for the dominant pitch in `window`,
    or (None, 0.0) if no pitch could be confidently detected.
    `spectrum` is `compute_spectrum(window)` (or a caller-shared
    equivalent), reused for FFT-based autocorrelation.

    `subharmonic_*` tune issue #69's octave-doubling correction below --
    see the comment at its call site for what it does and why."""
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
    else:
        # Issue #69: in the low register, a note's own fundamental can be
        # naturally weaker than its overtones (bass rolloff in real
        # speakers/mics, or just a harmonic-rich low tone) -- when it is,
        # a strong harmonic produces its own confident sub-threshold CMND
        # dip at an exact submultiple of the true fundamental's period,
        # and the scan above (ascending from tau_min) locks onto that
        # *shorter* lag first, reading one or two octaves too high.
        #
        # The true fundamental period, being a common multiple of every
        # harmonic's own period, produces a categorically *deeper* dip
        # than any lone harmonic's submultiple (confirmed empirically:
        # ~10x+ deeper in the reported failure cases) -- so check a few
        # small integer multiples of the found tau (candidate positions
        # for the true, longer fundamental period) and adopt the deepest
        # one that both clears the threshold and is meaningfully deeper
        # than the current candidate.
        #
        # Skipped entirely when the original candidate is already very
        # confident (near-zero CMND): an already-correct detection is
        # *also* trivially periodic at its own integer multiples (any
        # period-T signal repeats at 2T, 3T, ...), and integer-sample
        # rounding can occasionally make one of those multiples look
        # spuriously deeper than the true dip with no real periodicity
        # advantage -- gating on the original candidate's own confidence
        # avoids that regression (verified against octaves 3-5, see
        # tests/test_pitch_detect.py and docs/DECISIONS.md).
        _, tau_value = _parabolic_vertex(cmnd, tau)
        if tau_value > subharmonic_skip_cmnd:
            best_tau, best_value = tau, tau_value
            for multiple in range(2, subharmonic_max_multiple + 1):
                center = tau * multiple
                if center >= tau_max:
                    break
                lo = max(tau_min, center - 2)
                hi = min(tau_max - 1, center + 2)
                if lo > hi:
                    continue
                cand_tau = lo + int(np.argmin(cmnd[lo:hi + 1]))
                _, cand_value = _parabolic_vertex(cmnd, cand_tau)
                if cmnd[cand_tau] < threshold and cand_value < best_value * subharmonic_margin:
                    best_tau, best_value = cand_tau, cand_value
            tau = best_tau

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
