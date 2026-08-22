"""Octave-aware multi-pitch note detection via spectral peak-picking, per
docs spec (wayfinder #1, issue #10).

Spectral peak-picking with quadratic interpolation per peak (the same
technique pitch_detect.py uses for YIN's parabolic interpolation, applied
to spectral bins), plus harmonic-consistency pruning so a note's own
overtones aren't double-counted as separate notes.

Computes its own Hann-windowed FFT from the same ring-buffer window rather
than reusing pitch_detect.compute_spectrum()'s unwindowed spectrum: an
unwindowed (rectangular-window) FFT's slowly-decaying sidelobes are
strong enough, a semitone or more from a real peak, to register as
spurious extra "notes" -- verified empirically against this pipeline's
actual buffer/FFT sizes. A Hann window suppresses that ringing to a few
bins within a couple of percent, letting real local-maximum peak-picking
work. This costs one extra same-size FFT per hop, well inside the
latency budget's measured 7.6x-49x margin -- YIN's own shared, unwindowed
spectrum (pitch_detect.compute_spectrum) is untouched, so its calibrated
behavior is unaffected.
"""

from collections import namedtuple

import numpy as np

DEFAULT_MAX_NOTES = 6
DEFAULT_MIN_MAG_RATIO = 0.05
DEFAULT_HARMONIC_TOLERANCE_CENTS = 35.0
DEFAULT_MAX_PEAK_CANDIDATES = 20
DEFAULT_MIN_SEPARATION_CENTS = 50.0
DEFAULT_BASS_GATE_RATIO = 0.25
# Issue #68 residual: `_is_harmonic_of()` previously checked ANY integer
# harmonic_number = round(freq / accepted_freq), with no upper bound. That's
# fine for the low harmonics real instrument overtones (and this app's own
# chroma.HARMONIC_WEIGHTS folding) actually care about, but at higher
# multiples it starts pruning real, independent notes purely because they
# happen to land within harmonic_tolerance_cents of *some* large integer
# multiple of an already-accepted note -- with enough integers to try (8,
# 9, 10, 12...), an accidental near-miss becomes likely as chord density
# (and pitch spread) grows, which is exactly #68's "recall collapses under
# density" symptom. Capped at 4 to match the one convention this codebase
# already treats as "the harmonics that matter" -- chroma.HARMONIC_WEIGHTS
# (chroma.py) and YIN_SUBHARMONIC_MAX_MULTIPLE (config.py) both stop at the
# 4th harmonic too. See docs/DECISIONS.md for the empirical repro (a 6-note
# voicing where a real high note was pruned as another note's 12th
# harmonic, and a 5-note voicing losing one to a 6th-harmonic near-miss).
DEFAULT_HARMONIC_MAX_NUMBER = 4

NoteCandidate = namedtuple("NoteCandidate", ["pitch_class", "octave", "freq", "confidence"])


def _windowed_spectrum(window):
    w = len(window)
    size = 1
    while size < 2 * w:
        size *= 2
    tapered = np.asarray(window, dtype=np.float64) * np.hanning(w)
    return np.fft.rfft(tapered, size)


def _quadratic_interp_offset(y_prev, y_curr, y_next):
    denom = y_prev - 2 * y_curr + y_next
    if denom == 0:
        return 0.0
    return 0.5 * (y_prev - y_next) / denom


def _is_harmonic_of(freq, accepted_freq, tolerance_cents, max_harmonic_number=DEFAULT_HARMONIC_MAX_NUMBER):
    if accepted_freq <= 0:
        return False
    harmonic_number = round(freq / accepted_freq)
    if harmonic_number < 1 or harmonic_number > max_harmonic_number:
        return False
    predicted = accepted_freq * harmonic_number
    if predicted <= 0:
        return False
    return abs(1200 * np.log2(freq / predicted)) <= tolerance_cents


def _is_near_duplicate(freq, accepted_freq, tolerance_cents):
    if accepted_freq <= 0:
        return False
    return abs(1200 * np.log2(freq / accepted_freq)) <= tolerance_cents


def select_window(short_window, long_window, main_chroma, bass_chroma, gate_ratio=DEFAULT_BASS_GATE_RATIO):
    """Pick which ring buffer detect() should analyze this hop (issue #63).

    `short_window` (the app's real live WINDOW_SIZE) can't resolve two
    fundamentals as close together as an ordinary low triad's (e.g.
    C2+E2, ~17Hz apart) -- their Hann-window mainlobes physically overlap
    and merge into one wrong-frequency peak, no amount of interpolation
    recovers that. `long_window` (a bigger, equally up-to-date ring buffer
    the caller maintains alongside the short one) has enough resolution to
    separate them, at the cost of reflecting slightly older audio.

    Swapping to `long_window` unconditionally would pay that extra latency
    on every hop, including ordinary mid/treble-register playing that
    never needed it. Instead this gates on `bass_chroma` (chroma.
    fold_bass()'s <~250Hz-restricted vector) carrying real signal relative
    to `main_chroma`'s peak -- the same confidence-ratio convention
    chord_templates.match() already uses to decide whether a bass note is
    real or spectral-leakage noise -- so the extra latency is paid only on
    hops that plausibly have low content to resolve."""
    main_peak = float(np.max(main_chroma)) if len(main_chroma) else 0.0
    bass_peak = float(np.max(bass_chroma)) if len(bass_chroma) else 0.0
    if main_peak > 0 and bass_peak >= gate_ratio * main_peak:
        return long_window
    return short_window


def detect(
    window,
    sample_rate,
    max_notes=DEFAULT_MAX_NOTES,
    min_mag_ratio=DEFAULT_MIN_MAG_RATIO,
    harmonic_tolerance_cents=DEFAULT_HARMONIC_TOLERANCE_CENTS,
    max_peak_candidates=DEFAULT_MAX_PEAK_CANDIDATES,
    min_separation_cents=DEFAULT_MIN_SEPARATION_CENTS,
    harmonic_max_number=DEFAULT_HARMONIC_MAX_NUMBER,
):
    """Up to `max_notes` NoteCandidate entries for the notes sounding in
    `window` (the raw analysis ring buffer), magnitude-peak strongest
    first."""
    spectrum = _windowed_spectrum(window)
    magnitude = np.abs(spectrum)
    n = len(magnitude)
    if n < 3:
        return []
    fft_size = 2 * (n - 1)

    peak_indices = [i for i in range(1, n - 1) if magnitude[i] > magnitude[i - 1] and magnitude[i] > magnitude[i + 1]]
    if not peak_indices:
        return []

    global_max = max(magnitude[i] for i in peak_indices)
    if global_max <= 0:
        return []

    raw_candidates = []
    for i in peak_indices:
        if magnitude[i] < min_mag_ratio * global_max:
            continue
        offset = _quadratic_interp_offset(magnitude[i - 1], magnitude[i], magnitude[i + 1])
        freq = (i + offset) * sample_rate / fft_size
        raw_candidates.append((freq, magnitude[i]))

    raw_candidates.sort(key=lambda c: c[1], reverse=True)
    raw_candidates = raw_candidates[:max_peak_candidates]

    # Harmonic-consistency pruning walks candidates ascending by FREQUENCY,
    # not by magnitude (issue #67). `_is_harmonic_of(freq, acc_freq, ...)`
    # only prunes a candidate that's a harmonic *of* an already-accepted
    # one -- it has no reverse check for "is this already-accepted
    # candidate itself a harmonic of a not-yet-accepted, lower note".
    # Walking in magnitude order let a note's own higher harmonic jump the
    # queue ahead of its fundamental whenever the FFT happened to weight
    # that harmonic louder that hop -- routine under real acoustic capture
    # (mic/speaker frequency response, room-reflection comb filtering can
    # both null out a fundamental's bin and boost an overtone's), even
    # with zero frequency-estimate jitter: once the harmonic was accepted
    # first, the true fundamental arriving later isn't a harmonic of
    # anything higher in frequency, so it got accepted too, alongside the
    # phantom. Walking low-to-high instead means a real fundamental always
    # gets first crack at a slot, so its own harmonics reliably prune
    # against it afterward regardless of which partial carried more raw
    # magnitude that hop. Confirmed empirically: a synthesized E4 whose 3rd
    # harmonic (partial amplitude 1.4 vs. fundamental's 1.0) out-magnitudes
    # its own fundamental reproduces exactly this issue's B5-ghost-note
    # symptom under the old magnitude-first ordering, and is fixed by this
    # reordering alone -- no tolerance widening needed (a synthetic sweep
    # up to 30 cents of pure frequency detuning, fundamental still
    # loudest, never broke the existing 35-cent tolerance; the reported
    # real-world failures are this ordering bug, not an undersized
    # tolerance).
    freq_ordered = sorted(raw_candidates, key=lambda c: c[0])
    pruned = []
    for freq, mag in freq_ordered:
        if any(_is_near_duplicate(freq, acc_freq, min_separation_cents) for acc_freq, _ in pruned):
            continue
        if any(_is_harmonic_of(freq, acc_freq, harmonic_tolerance_cents, harmonic_max_number) for acc_freq, _ in pruned):
            continue
        pruned.append((freq, mag))

    if not pruned:
        return []

    # Cap to max_notes by magnitude now that harmonics are pruned -- keep
    # whichever surviving fundamentals are strongest, not just whichever
    # happen to sit lowest in frequency.
    pruned.sort(key=lambda c: c[1], reverse=True)
    accepted = pruned[:max_notes]

    top_mag = accepted[0][1]
    notes = []
    for freq, mag in accepted:
        midi = 69 + 12 * np.log2(freq / 440.0)
        rounded = round(midi)
        pitch_class = int(rounded % 12)
        octave = int(rounded // 12 - 1)
        confidence = float(min(1.0, mag / top_mag))
        notes.append(NoteCandidate(pitch_class=pitch_class, octave=octave, freq=freq, confidence=confidence))
    return notes
