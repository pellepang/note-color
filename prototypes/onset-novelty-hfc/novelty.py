"""HFC and complex-domain onset novelty measures (detection-systems-survey.md
S3 near-term recommendation 1), matching onset_detect.py's existing style:
pure, None-safe, operating directly on pitch_detect.compute_spectrum()
output. Neither function is added to onset_detect.py itself -- see the
repo's prototype convention (prototypes/issue-42-menu-animation/): this
directory is standalone and does not modify any existing source file.

Both functions mirror spectral_flux()'s half-wave-rectified-positive-
difference shape (onset_detect.py:12-70) so they're drop-in comparable at
the exact same call sites spectral_flux() already has (note_smoother.py's
onset gate, tempo_tracker.py's novelty history).
"""

import numpy as np


def hfc_novelty(spectrum, prev_spectrum):
    """aubio-style High-Frequency Content novelty. HFC(n) = sum_k k*|X_k(n)|
    weights each bin's magnitude by its bin index (proportional to
    frequency) -- a percussive/broadband transient's energy skews toward
    high bins far more than a sustained tone's, so weighting by bin index
    before differencing makes a genuine onset's rise stand out more than
    spectral_flux()'s equal-weight-per-bin sum does. Reported in aubio's
    own onset-detection-function comparison (Brossier 2006, cited in
    docs/research/oss-landscape-rhythm-tempo.md) as 0.750 vs. plain
    spectral-difference's 0.647-0.672.

    Like spectral_flux(), takes the half-wave-rectified positive difference
    of the *weighted* magnitude sum between two consecutive frames (rather
    than aubio's own convention of peak-picking the raw per-frame HFC(n)
    curve directly) -- this keeps it a drop-in-comparable measure at
    spectral_flux()'s exact call sites, both of which are already framed as
    "novelty since last hop," not "raw energy this hop." `prev_spectrum`
    None (first hop) or shape-mismatched (a ring buffer size change)
    returns 0.0, exactly mirroring spectral_flux()'s own contract.

    Normalized by the previous frame's own weighted-energy total, exactly
    matching spectral_flux()'s self-relative normalization (onset_detect.py's
    docstring: issue #66 -- a raw/absolute measure conflates loud-note
    "wobble" with quiet-note "attack" at any single fixed threshold; this
    prototype's own comparison harness independently re-confirmed the same
    failure mode building this file -- an unnormalized version let one loud
    silence->tone transition's raw weighted-magnitude jump dominate an
    adaptive mean+std threshold computed over a whole run, starving out
    every quieter genuine onset later in the same run)."""
    if prev_spectrum is None:
        return 0.0
    mag = np.abs(spectrum)
    prev_mag = np.abs(prev_spectrum)
    if mag.shape != prev_mag.shape:
        return 0.0
    weights = np.arange(len(mag), dtype=np.float64)
    hfc = float(np.sum(weights * mag))
    prev_hfc = float(np.sum(weights * prev_mag))
    raw = max(0.0, hfc - prev_hfc)
    if prev_hfc <= 0.0:
        return raw
    return raw / prev_hfc


def complex_domain_novelty(spectrum, prev_spectrum, prev_prev_spectrum):
    """Complex-domain onset novelty (Duxbury/Bello-style "complex spectral
    difference"; aubio's `complex` ODF, 0.700 in the same comparison
    hfc_novelty() cites above -- both clearly beat plain spectral
    difference's 0.647-0.672).

    Unlike spectral_flux()/hfc_novelty() (magnitude-only, two frames),
    this predicts each bin's expected complex value for the current frame
    from the PREVIOUS TWO frames -- constant-magnitude, linearly
    extrapolated phase (predicted_phase = 2*phase(n-1) - phase(n-2)) -- and
    measures the actual frame's Euclidean deviation from that prediction,
    summed over bins. A steady sustained tone's phase advances linearly
    hop-to-hop (predictable, low novelty even though magnitude is
    constant); a genuine onset breaks that linear-phase assumption (a new
    partial's phase is uncorrelated with what came before), so this can
    register an onset that pure magnitude-difference measures miss
    entirely -- e.g. a new note starting at the same magnitude as a
    decaying previous one. This is a genuinely different SHAPE of signal
    than spectral_flux()/hfc_novelty(): those need one previous frame,
    this needs two, which is a real integration cost -- see README.

    None-safe for both `prev_spectrum` (first hop) and `prev_prev_spectrum`
    (first two hops) -- returns 0.0 for either, same "no novelty yet"
    convention as spectral_flux(). Shape-mismatched frames (any pair)
    likewise return 0.0.

    Normalized by the previous frame's total magnitude, same self-relative
    convention as spectral_flux()/hfc_novelty() above and for the same
    reason -- an unnormalized Euclidean deviation sum scales with the
    signal's raw amplitude, so a single loud onset's deviation can dwarf
    every quieter onset's in the same run under a shared threshold."""
    if prev_spectrum is None or prev_prev_spectrum is None:
        return 0.0
    spectrum = np.asarray(spectrum)
    prev_spectrum = np.asarray(prev_spectrum)
    prev_prev_spectrum = np.asarray(prev_prev_spectrum)
    if spectrum.shape != prev_spectrum.shape or spectrum.shape != prev_prev_spectrum.shape:
        return 0.0

    prev_mag = np.abs(prev_spectrum)
    prev_phase = np.angle(prev_spectrum)
    prev_prev_phase = np.angle(prev_prev_spectrum)

    predicted_phase = 2 * prev_phase - prev_prev_phase
    predicted = prev_mag * np.exp(1j * predicted_phase)

    deviation = float(np.sum(np.abs(spectrum - predicted)))
    prev_energy = float(np.sum(prev_mag))
    if prev_energy <= 0.0:
        return deviation
    return deviation / prev_energy
