"""Onset-novelty measures shared by note_smoother.py's onset gate and
tempo_tracker.py's beat tracker (issue #55).

Both functions are pure (no state) -- callers hold prev_spectrum/
prev_chroma between hops themselves, mirroring how NoteSmoother already
holds its own prev_rms rather than pitch_detect.py holding it.
"""

import numpy as np


def spectral_flux(spectrum, prev_spectrum):
    """Half-wave-rectified sum of positive per-bin magnitude differences
    between two consecutive frames of pitch_detect.compute_spectrum()
    output -- a standard onset-novelty measure: energy that increased
    since last hop (a new note's fresh harmonics) counts, energy that
    merely decayed (an existing note dying away) doesn't.

    `prev_spectrum` is None on the very first hop (nothing to diff
    against yet) -- returns 0.0 rather than raising, same "no novelty
    yet" convention a mismatched shape (a ring buffer size change) also
    falls back to."""
    if prev_spectrum is None:
        return 0.0
    mag = np.abs(spectrum)
    prev_mag = np.abs(prev_spectrum)
    if mag.shape != prev_mag.shape:
        return 0.0
    diff = mag - prev_mag
    return float(np.sum(diff[diff > 0]))


def chroma_flux(chroma, prev_chroma):
    """Same half-wave-rectified positive-difference computation as
    spectral_flux(), applied to two 12-bin chroma.fold() vectors instead
    of a full spectrum -- a much cheaper, polyphonic-flavored novelty
    signal, used by tempo_tracker.py rather than the monophonic onset
    gate (see note_smoother.py)."""
    if prev_chroma is None:
        return 0.0
    chroma = np.asarray(chroma, dtype=np.float64)
    prev_chroma = np.asarray(prev_chroma, dtype=np.float64)
    if chroma.shape != prev_chroma.shape:
        return 0.0
    diff = chroma - prev_chroma
    return float(np.sum(diff[diff > 0]))
