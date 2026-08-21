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
    output, normalized by the previous frame's total magnitude -- a
    standard onset-novelty measure: energy that increased since last hop
    (a new note's fresh harmonics) counts, energy that merely decayed (an
    existing note dying away) doesn't.

    Normalized to a *relative* (fraction of prior total energy) measure
    rather than an absolute one (issue #66) -- a raw, unnormalized sum
    over a real ~2000-bin spectrum sits two to three orders of magnitude
    above config.ONSET_FLUX_THRESHOLD, a value tuned by eye against tiny
    3-element test arrays rather than a realistic full-size spectrum;
    with that scale mismatch, a perfectly sustained tone's natural
    hop-to-hop phase wobble (the unwindowed shared spectrum's leakage
    sidelobes shift as a periodic tone's phase slides across the window)
    alone cleared the threshold, so is_onset misfired on nearly every hop
    instead of only at a real attack.

    Normalizing by bin count alone (dividing the raw sum by len(diff))
    fixes the window-size dependence but NOT amplitude dependence -- both
    the raw sum and a plain per-bin mean scale linearly with signal
    amplitude, so a fixed threshold that's high enough to reject a loud
    note's steady-state wobble is also too high to catch a quiet note's
    genuine attack (verified empirically: the ratio between a steady
    tone's worst-case wobble and a genuine attack's flux stays ~4x
    regardless of amplitude under bin-count normalization alone, so a
    loud sustained note's wobble can still outscore a quiet attack in
    absolute terms). Dividing by the previous frame's own total magnitude
    instead makes the measure self-relative: both numerator and
    denominator scale together with the signal's actual loudness, so the
    result stays threshold-comparable across quiet and loud playing
    alike, not just across window sizes. Empirically (synthesized tones
    across this app's ~4-octave range and 0.02-1.0 amplitude): a
    perfectly sustained tone's worst-case relative flux tops out around
    ~0.3; a genuine attack (even a quiet one near the silence gate)
    clears ~0.33 and quickly climbs into the low single digits for
    normal playing volume -- see config.ONSET_FLUX_THRESHOLD's comment.

    `prev_spectrum` is None on the very first hop (nothing to diff
    against yet) -- returns 0.0 rather than raising, same "no novelty
    yet" convention a mismatched shape (a ring buffer size change) also
    falls back to. A previous frame with exactly zero total magnitude
    (silence, or a synthetic all-zero test spectrum) has no energy to be
    "a fraction of" -- falls back to the raw (unnormalized) sum, which is
    itself 0.0 whenever the new frame is *also* all-zero, and otherwise
    unambiguously novel (any energy at all, compared to none)."""
    if prev_spectrum is None:
        return 0.0
    mag = np.abs(spectrum)
    prev_mag = np.abs(prev_spectrum)
    if mag.shape != prev_mag.shape:
        return 0.0
    diff = mag - prev_mag
    raw = float(np.sum(diff[diff > 0]))
    prev_energy = float(np.sum(prev_mag))
    if prev_energy <= 0.0:
        return raw
    return raw / prev_energy


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
