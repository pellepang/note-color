"""Comparison harness: synthesize a note sequence with known ground-truth
onset times (extending tests/test_onset_detect.py's synthesis convention --
a real config.WINDOW_SIZE/config.BLOCK_SIZE hop loop, not isolated
two-frame snippets), run the app's real onset_detect.spectral_flux()
alongside this prototype's hfc_novelty()/complex_domain_novelty()
(novelty.py) hop-by-hop, peak-pick each novelty series with the same
adaptive-threshold rule, and report detected-vs-ground-truth onset timing
accuracy (hit rate, false positives, mean absolute timing error) per
method.

Run: .venv/bin/python prototypes/onset-novelty-hfc/harness.py
"""

import os
import sys

import numpy as np

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import config  # noqa: E402
from pitch_detect import compute_spectrum  # noqa: E402
from onset_detect import spectral_flux  # noqa: E402

from novelty import complex_domain_novelty, hfc_novelty  # noqa: E402

SAMPLE_RATE = config.SAMPLE_RATE
BLOCK_SIZE = config.BLOCK_SIZE
WINDOW_SIZE = config.WINDOW_SIZE


def _segment(freq, duration, amplitude=0.3, harmonics=(1.0,), sample_rate=SAMPLE_RATE):
    """One note (or silence, freq=None) segment -- same
    amplitude*sum(harmonic sines) synthesis convention as
    tests/test_pitch_detect.py's make_tone(), extended with an explicit
    amplitude so a melody's notes aren't all identically loud (more
    realistic, and stresses the novelty measures' normalization)."""
    n = int(sample_rate * duration)
    if freq is None:
        return np.zeros(n, dtype=np.float64)
    t = np.arange(n) / sample_rate
    signal = np.zeros(n)
    for i, h_amp in enumerate(harmonics, start=1):
        signal += h_amp * np.sin(2 * np.pi * freq * i * t)
    signal *= amplitude / sum(harmonics)
    return signal.astype(np.float64)


# A short melody with known onset times: a mix of legato (immediate note
# change, no gap -- the hardest case, since there's no silence to make the
# attack obvious) and gapped transitions (silence -> tone), spanning both a
# normal register and a low, harmonic-rich register (issue #69's register,
# included here since it's this project's own documented hard case for
# pitch tracking -- worth checking onset detection isn't quietly worse
# there too).
SEQUENCE = [
    ("silence", None, 0.15, 0.0, (1.0,)),
    ("A3 (legato start)", 220.0, 0.40, 0.30, (1.0, 0.5, 0.25)),
    ("E4 (legato transition, no gap)", 330.0, 0.40, 0.30, (1.0, 0.5, 0.25)),
    ("silence", None, 0.12, 0.0, (1.0,)),
    ("A2 low-register harmonic-rich (gapped)", 110.0, 0.40, 0.25, (1.0, 0.6, 0.4, 0.2)),
    ("silence", None, 0.10, 0.0, (1.0,)),
    ("A4 plain sine, quiet (gapped, quiet attack)", 440.0, 0.35, 0.08, (1.0,)),
]

# Ground truth onset times: the start of any segment whose freq differs
# from the immediately preceding segment's freq (covers both gapped
# silence->tone transitions and legato tone->tone transitions; silence->
# silence or repeating the same freq would not be a genuine onset, though
# this sequence has no such case).
waveform_parts = []
ground_truth = []
cursor_samples = 0
prev_freq = None
for label, freq, duration, amp, harmonics in SEQUENCE:
    seg = _segment(freq, duration, amp, harmonics)
    if freq is not None and freq != prev_freq:
        ground_truth.append(cursor_samples / SAMPLE_RATE)
    waveform_parts.append(seg)
    cursor_samples += len(seg)
    prev_freq = freq
waveform = np.concatenate(waveform_parts)


def detect_onsets(novelty_series, hop_times, k_mad=8.0, refractory_s=0.08):
    """Generic adaptive-threshold peak-picker: threshold = median + k_mad *
    median-absolute-deviation of the WHOLE run (not tuned per-signal-type
    by hand -- this keeps the comparison fair across three measures with
    very different raw scales). Median/MAD, not mean/std: an early build of
    this harness used mean+3*std and found it dominated by exactly ONE
    outlier hop -- the very first onset in the test sequence starts from
    genuine digital silence, where every novelty measure's own None-safe
    "zero prior energy" fallback (see novelty.py's/onset_detect.py's
    docstrings) reports a large *unnormalized* value by construction (there
    is no prior energy to be a normalized fraction OF). A single such
    outlier inflates mean+std enough to starve out every quieter, genuine
    mid-run onset under the same threshold; median/MAD is the standard
    robust-statistics fix for exactly this (a handful of literature
    onset-detection systems, e.g. SuperFlux, use median-filtered adaptive
    thresholds for the same reason). A refractory period after each
    accepted onset prevents one attack's transient (which can clear
    threshold for 2-3 consecutive hops) from being counted as several
    onsets."""
    arr = np.asarray(novelty_series)
    median = float(np.median(arr))
    mad = float(np.median(np.abs(arr - median)))
    threshold = median + k_mad * mad
    detected = []
    last_t = -1e9
    for value, t in zip(arr, hop_times):
        if value > threshold and (t - last_t) >= refractory_s:
            detected.append(t)
            last_t = t
    return detected, threshold


def best_over_k_mad(novelty_series, hop_times, truth, k_mad_grid=(3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 40)):
    """Each novelty measure has its own noise character (config.py's own
    ONSET_FLUX_THRESHOLD is itself a hand-tuned, method-specific constant,
    not a shared one) -- sweep k_mad per method and report the setting
    that maximizes hits while minimizing misses+false_positives (a simple
    hits-misses-false_positives score), rather than unfairly penalizing
    whichever method happens to need a stricter multiplier at the same
    k_mad. This mirrors how a real deployment would calibrate each ODF
    separately, same as this project's own provisional-constants posture
    elsewhere (CLAUDE.md's Known limitations: "ONSET_FLUX_THRESHOLD... not
    yet tuned against extended real playing")."""
    best = None
    for k in k_mad_grid:
        detected, threshold = detect_onsets(novelty_series, hop_times, k_mad=k)
        hits, misses, fp, mean_err = score(detected, truth)
        quality = hits - misses - fp
        if best is None or quality > best[0]:
            best = (quality, k, threshold, detected, hits, misses, fp, mean_err)
    return best


def score(detected, truth, tolerance_s=0.15):
    """Nearest-match scoring: each truth onset is matched to its closest
    detected onset within `tolerance_s`; unmatched truth onsets are misses,
    unmatched detections are false positives. Returns (hits, misses,
    false_positives, mean_abs_error_ms over matched pairs)."""
    remaining = list(detected)
    hits = 0
    errors = []
    for t in truth:
        if not remaining:
            continue
        nearest = min(remaining, key=lambda d: abs(d - t))
        if abs(nearest - t) <= tolerance_s:
            hits += 1
            errors.append(abs(nearest - t) * 1000.0)
            remaining.remove(nearest)
    misses = len(truth) - hits
    false_positives = len(remaining)
    mean_err = float(np.mean(errors)) if errors else None
    return hits, misses, false_positives, mean_err


def main():
    ring = np.zeros(WINDOW_SIZE, dtype=np.float64)
    prev_spectrum = None
    prev_prev_spectrum = None

    flux_series, hfc_series, complex_series, hop_times = [], [], [], []

    n = len(waveform)
    for i in range(0, n, BLOCK_SIZE):
        block = waveform[i:i + BLOCK_SIZE]
        if len(block) < BLOCK_SIZE:
            block = np.concatenate([block, np.zeros(BLOCK_SIZE - len(block))])
        ring = np.concatenate([ring[len(block):], block])
        spectrum = compute_spectrum(ring)

        flux_series.append(spectral_flux(spectrum, prev_spectrum))
        hfc_series.append(hfc_novelty(spectrum, prev_spectrum))
        complex_series.append(complex_domain_novelty(spectrum, prev_spectrum, prev_prev_spectrum))
        hop_times.append((i + BLOCK_SIZE) / SAMPLE_RATE)

        prev_prev_spectrum = prev_spectrum
        prev_spectrum = spectrum

    print(f"Sequence: {len(SEQUENCE)} segments, {n} samples ({n / SAMPLE_RATE:.2f}s), "
          f"{len(hop_times)} hops of {BLOCK_SIZE} samples each.")
    print(f"Ground truth onsets (s): {[f'{t:.3f}' for t in ground_truth]}")
    print()

    print(f"{'method':16s} {'best_k_mad':>10s} {'threshold':>10s} {'hits':>5s} {'misses':>7s} "
          f"{'false_pos':>10s} {'mean_err_ms':>12s}")
    print("-" * 90)
    for name, series in [("spectral_flux", flux_series), ("hfc_novelty", hfc_series),
                          ("complex_domain", complex_series)]:
        quality, k, threshold, detected, hits, misses, fp, mean_err = best_over_k_mad(series, hop_times, ground_truth)
        mean_err_str = f"{mean_err:.1f}" if mean_err is not None else "n/a"
        print(f"{name:16s} {k:>10d} {threshold:>10.4f} {hits:>5d} {misses:>7d} {fp:>10d} {mean_err_str:>12s}")
        print(f"{'':16s} detected onsets (s): {[f'{t:.3f}' for t in detected]}")
        print()

    print(f"({len(ground_truth)} ground-truth onsets total; hit tolerance = 150ms; "
          f"refractory period = 80ms; hop = {BLOCK_SIZE / SAMPLE_RATE * 1000:.1f}ms; "
          f"each method's k_mad independently swept over {(3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 40)} "
          f"and best-scoring reported)")


if __name__ == "__main__":
    main()
