"""Timing + chord-recognition-accuracy comparison: chroma.fold() (current,
shipped) vs. nnls_chroma() (this prototype, issue #81) -- both fed into
the REAL, unmodified chord_templates.match(), so any accuracy difference
is attributable to the chroma-folding step alone, not a different matcher.

Usage:
    .venv/bin/python prototypes/nnls-chroma/harness.py
"""

import os
import sys
import time

import numpy as np

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import chord_templates
import chroma
import config
from pitch_detect import compute_spectrum

from nnls_chroma import nnls_chroma

SAMPLE_RATE = config.SAMPLE_RATE


def freq_for(pitch_class, octave):
    midi = (octave + 1) * 12 + pitch_class
    return 440.0 * 2 ** ((midi - 69) / 12)


def make_chord_signal(notes, sample_rate=SAMPLE_RATE, duration=None, harmonics=(1.0, 0.5, 1 / 3, 0.25)):
    """notes: list of (pitch_class, octave). Additive synthesis, harmonics
    1-4 weighted like chroma.py's own HARMONIC_WEIGHTS -- matches this
    repo's existing acoustic-test convention (synth_notes() in
    scripts/acoustic_pipeline_test.py), not a bare sine."""
    duration = duration or (config.WINDOW_SIZE / sample_rate)
    n = int(sample_rate * duration)
    t = np.arange(n) / sample_rate
    out = np.zeros(n)
    for pc, octave in notes:
        f0 = freq_for(pc, octave)
        for h, amp in enumerate(harmonics, start=1):
            out += amp * np.sin(2 * np.pi * f0 * h * t)
    return out


# --------------------------------------------------------------------
# Timing
# --------------------------------------------------------------------

def time_methods(n_trials=200):
    notes = [(0, 4), (4, 4), (7, 4)]  # C major triad
    signal = make_chord_signal(notes)
    spectrum = compute_spectrum(signal)

    # warm caches (weighting matrix / NNLS dictionary construction)
    chroma.fold(spectrum, SAMPLE_RATE)
    nnls_chroma(spectrum, SAMPLE_RATE)

    t0 = time.perf_counter()
    for _ in range(n_trials):
        chroma.fold(spectrum, SAMPLE_RATE)
    fold_time = (time.perf_counter() - t0) / n_trials

    t0 = time.perf_counter()
    for _ in range(n_trials):
        nnls_chroma(spectrum, SAMPLE_RATE)
    nnls_time = (time.perf_counter() - t0) / n_trials

    print("== Per-hop cost (this machine, desktop-class -- NOT Pi-measured) ==")
    print(f"chroma.fold():  {fold_time * 1000:.3f} ms/hop  ({n_trials} trials)")
    print(f"nnls_chroma():  {nnls_time * 1000:.3f} ms/hop  ({n_trials} trials)")
    print(f"ratio: {nnls_time / fold_time:.1f}x slower than the current linear fold")
    print(f"(hop budget: config.BLOCK_SIZE/config.SAMPLE_RATE = "
          f"{1000 * config.BLOCK_SIZE / config.SAMPLE_RATE:.1f} ms; "
          f"end-to-end latency target is comfortably under 150ms, see CLAUDE.md)")
    print()


# --------------------------------------------------------------------
# Chord-recognition accuracy
# --------------------------------------------------------------------

CHORD_CASES = [
    ("C major triad", [(0, 4), (4, 4), (7, 4)], "C"),
    ("A minor triad", [(9, 3), (0, 4), (4, 4)], "A-"),
    ("G dominant 7", [(7, 3), (11, 3), (2, 4), (5, 4)], "G7"),
    ("D major 7", [(2, 4), (6, 4), (9, 4), (1, 5)], "DΔ7"),
    ("dense 6-note (safety-margin voicing, from test_multipitch.py)", [
        (0, 2), (4, 2), (7, 2), (10, 3), (2, 4), (6, 4),
    ], None),  # not a clean template match by design -- just checks stability, no name asserted
]


def harmonic_near_miss_case():
    """The project's own documented open limitation (docs/DECISIONS.md,
    CLAUDE.md's Known limitations): a root and another note landing within
    ~2 cents of that root's own 3rd harmonic. A 12-TET fifth-plus-octave
    above a root lands ~2 cents from the true 3rd harmonic -- e.g. C3's
    3rd harmonic is at 3*130.81=392.4Hz; G4 (a fifth+octave above C3) is
    392.0Hz, ~1.8 cents away. Ground truth: C3 root + G4 (its own
    fifth-plus-octave) + E3 (making a real C major triad, root position,
    with the near-miss note ALSO present) -- 3 real notes, one of them
    spectrally near-coincident with another's harmonic."""
    c3 = freq_for(0, 3)
    e3 = freq_for(4, 3)
    g4 = freq_for(7, 4)
    n = int(config.WINDOW_SIZE)
    t = np.arange(n) / SAMPLE_RATE
    harmonics = (1.0, 0.5, 1 / 3, 0.25)
    out = np.zeros(n)
    for f0 in (c3, e3, g4):
        for h, amp in enumerate(harmonics, start=1):
            out += amp * np.sin(2 * np.pi * f0 * h * t)
    return out, {0, 4, 7}, "C (root-position triad, with a near-3rd-harmonic-coincident 5th an octave up)"


def run_accuracy():
    print("== Chord-recognition accuracy: chroma.fold() vs. nnls_chroma(), both -> REAL chord_templates.match() ==")
    header = f"{'case':<60}{'expected':<10}{'fold':<10}{'nnls':<10}"
    print(header)
    print("-" * len(header))

    for name, notes, expected in CHORD_CASES:
        signal = make_chord_signal(notes)
        spectrum = compute_spectrum(signal)

        fold_vec = chroma.fold(spectrum, SAMPLE_RATE)
        nnls_vec = nnls_chroma(spectrum, SAMPLE_RATE)

        fold_name = chord_templates.match(fold_vec)
        nnls_name = chord_templates.match(nnls_vec)

        print(f"{name:<60}{str(expected):<10}{str(fold_name):<10}{str(nnls_name):<10}")

    print()
    print("== Harmonic-near-miss case (this project's own documented open limitation) ==")
    signal, expected_pcs, expected_name = harmonic_near_miss_case()
    spectrum = compute_spectrum(signal)
    fold_vec = chroma.fold(spectrum, SAMPLE_RATE)
    nnls_vec = nnls_chroma(spectrum, SAMPLE_RATE)
    fold_name = chord_templates.match(fold_vec)
    nnls_name = chord_templates.match(nnls_vec)
    print(f"expected: {expected_name} (pcs={sorted(expected_pcs)})")
    print(f"fold():   {fold_name}   chroma={np.round(fold_vec, 3).tolist()}")
    print(f"nnls():   {nnls_name}   chroma={np.round(nnls_vec, 3).tolist()}")
    # Which pitch classes does each method rank in its top-3 by chroma weight?
    fold_top3 = sorted(range(12), key=lambda pc: -fold_vec[pc])[:3]
    nnls_top3 = sorted(range(12), key=lambda pc: -nnls_vec[pc])[:3]
    print(f"fold() top-3 pcs by weight: {fold_top3}  (expected {sorted(expected_pcs)})")
    print(f"nnls() top-3 pcs by weight: {nnls_top3}  (expected {sorted(expected_pcs)})")


if __name__ == "__main__":
    time_methods()
    run_accuracy()
