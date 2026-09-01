"""NNLS-based approximate note transcription ahead of chroma folding
(issue #81; Mauch & Dixon, ISMIR 2010's NNLS-Chroma/Chordino approach).

Standalone, self-contained -- does not modify chroma.py or any other
existing file, same prototype convention as
prototypes/detection-backend-protocol/ and prototypes/onset-novelty-hfc/.

The idea: chroma.fold() computes a chroma vector as `matrix @ magnitude`,
a fixed LINEAR projection -- every candidate pitch class's Gaussian
weighting harvests whatever spectral energy falls near its own harmonics,
independently of every other candidate. Two different notes that share a
near-coincident harmonic (this project's own documented "harmonic_number
<=4 near-exact collision" limitation, docs/DECISIONS.md) both "claim" that
same energy in the linear-fold approach, since neither candidate's
contribution is fit jointly against the others.

NNLS-chroma instead treats the spectrum as a SUM of candidate notes' own
known harmonic profiles and solves a constrained least-squares problem for
how much of each candidate note is actually present -- `scipy.optimize.
nnls(D, magnitude)` finds x >= 0 minimizing ||D @ x - magnitude||^2, where
each column of D is one candidate (real note, not just pitch class)'s
expected harmonic-weighted spectral profile. Two notes competing for the
same spectral bin now have to jointly explain that bin's total energy
(least-squares residual), rather than each independently harvesting a
share of it via a fixed weighting -- the mechanism Mauch & Dixon's paper
reports as the source of their measured +12pp on harmonically-ambiguous
chords.
"""

import numpy as np
from scipy.optimize import nnls

HARMONICS = (1, 2, 3, 4)
HARMONIC_WEIGHTS = {1: 1.0, 2: 0.5, 3: 1.0 / 3, 4: 0.25}
GAUSSIAN_SIGMA_SEMITONES = 0.25
# Real notes, not pitch classes -- MIDI 24 (C1) to 96 (C7), matching this
# project's own FMIN/FMAX-adjacent working range (config.py's YIN range is
# roughly E1-D#6). A per-note dictionary, one column per real pitch, is
# what lets NNLS jointly fit distinct octaves of the same pitch class
# separately before folding to 12 bins -- folding to 12 pitch classes
# first (as chroma.fold() does) would throw away exactly the octave
# information NNLS needs to jointly resolve real notes against harmonics.
MIDI_LOW, MIDI_HIGH = 24, 96

_dict_cache = {}


def _note_dictionary(sample_rate, n_bins):
    """(D, pitch_classes): D is (n_bins, n_notes), one column per MIDI note
    in [MIDI_LOW, MIDI_HIGH], its harmonic-weighted Gaussian profile in the
    spectrum -- same per-harmonic Gaussian shape chroma._weighting_matrix()
    uses, just not yet summed across octaves into 12 bins. pitch_classes is
    the parallel (n_notes,) array of each column's pitch class, used to
    fold the NNLS solution down to a chroma vector afterward."""
    key = (sample_rate, n_bins)
    cached = _dict_cache.get(key)
    if cached is not None:
        return cached

    fft_size = 2 * (n_bins - 1)
    freqs = np.fft.rfftfreq(fft_size, d=1.0 / sample_rate)
    nonzero = freqs > 0
    log_freqs = np.zeros(n_bins)
    log_freqs[nonzero] = np.log2(freqs[nonzero])
    sigma = GAUSSIAN_SIGMA_SEMITONES / 12.0
    nyquist = sample_rate / 2.0

    midis = list(range(MIDI_LOW, MIDI_HIGH + 1))
    D = np.zeros((n_bins, len(midis)))
    pitch_classes = np.zeros(len(midis), dtype=int)
    for col, midi in enumerate(midis):
        pitch_classes[col] = midi % 12
        f0 = 440.0 * 2 ** ((midi - 69) / 12.0)
        for harmonic in HARMONICS:
            target = f0 * harmonic
            if target <= 0 or target >= nyquist:
                continue
            log_target = np.log2(target)
            weight = np.exp(-0.5 * ((log_freqs - log_target) / sigma) ** 2)
            weight[~nonzero] = 0.0
            D[:, col] += weight * HARMONIC_WEIGHTS[harmonic]

    _dict_cache[key] = (D, pitch_classes)
    return D, pitch_classes


def nnls_chroma(spectrum, sample_rate, max_iter=None):
    """12-element chroma vector via NNLS note-activation fit, folded from
    real per-note activations to pitch classes (summed across octaves,
    mirroring chroma.fold()'s own final shape so it's a drop-in-comparable
    output for chord_templates.match())."""
    magnitude = np.abs(np.asarray(spectrum))
    D, pitch_classes = _note_dictionary(sample_rate, len(magnitude))
    kwargs = {} if max_iter is None else {"maxiter": max_iter}
    activations, _residual = nnls(D, magnitude, **kwargs)
    chroma_vec = np.zeros(12)
    np.add.at(chroma_vec, pitch_classes, activations)
    return chroma_vec
