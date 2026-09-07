"""Pluggable seam for the two live detection entry points `analysis_loop()`
calls every hop -- monophonic pitch and polyphonic multipitch. Per
`docs/research/architecture-modernization-plan.md` §3.1: `NoteSmoother`/
`ChordSmoother`/`DurationTracker` already only depend on these two return
shapes (`(freq_hz | None, confidence)` and a `list[multipitch.NoteCandidate]`),
not on how they were produced -- so a `Protocol` capturing exactly those
shapes lets a future alternative algorithm (pYIN, NNLS-chroma, ...) slot in
behind `SessionState` without a second surgery through `analysis_loop()`'s
per-hop body.

Deliberately just two `Protocol`s + adapter classes wrapping today's
`pitch_detect.detect_pitch()`/`multipitch.detect()` -- not a plugin
framework, not `setuptools` entry points (this repo controls all backend
code, so a lighter interface-based seam is the right tool per Python's own
packaging guide). `YinBackend`/`SpectralPeakBackend` capture their
algorithm-specific config once at construction instead of threading it
through every call, which is the part that actually buys pluggability: it
moves "which config constants this algorithm needs" out of `analysis_loop()`
and into the backend object itself. `multipitch.select_window()` (issue
#63's bass-gated long-window logic) stays a spectral-peak-picking-specific
concern called directly from `analysis_loop()`, not folded into this
Protocol -- it's not a shape any other algorithm has an equivalent to yet,
and padding the Protocol with speculative params before a second real
backend exists would defeat the point of keeping it minimal.
"""

from typing import Optional, Protocol

import numpy as np

import multipitch
from pitch_detect import detect_pitch


class MonoPitchBackend(Protocol):
    def detect(self, ring: np.ndarray, spectrum: np.ndarray, sample_rate: int) -> tuple[Optional[float], float]:
        """-> (freq_hz or None, confidence 0..1). Mirrors
        pitch_detect.detect_pitch()'s return shape."""
        ...


class PolyphonicBackend(Protocol):
    def detect(self, window: np.ndarray, sample_rate: int) -> list:
        """-> list[multipitch.NoteCandidate]. Mirrors multipitch.detect()'s
        return shape -- NoteSmoother/DurationTracker/ChordSmoother already
        only depend on this shape, not on how it was produced."""
        ...


class YinBackend:
    """Wraps pitch_detect.detect_pitch() -- today's only MonoPitchBackend,
    and analysis_loop()'s default. All of YIN's tuning knobs are captured
    here at construction time instead of threaded through every detect()
    call."""

    def __init__(self, fmin, fmax, threshold, subharmonic_max_multiple, subharmonic_margin, subharmonic_skip_cmnd):
        self.fmin = fmin
        self.fmax = fmax
        self.threshold = threshold
        self.subharmonic_max_multiple = subharmonic_max_multiple
        self.subharmonic_margin = subharmonic_margin
        self.subharmonic_skip_cmnd = subharmonic_skip_cmnd

    def detect(self, ring, spectrum, sample_rate):
        return detect_pitch(
            ring, sample_rate, spectrum, self.fmin, self.fmax, self.threshold,
            self.subharmonic_max_multiple, self.subharmonic_margin, self.subharmonic_skip_cmnd,
        )


class SpectralPeakBackend:
    """Wraps multipitch.detect() -- today's only PolyphonicBackend, and
    analysis_loop()'s default. `window` is passed in already selected
    (multipitch.select_window()'s bass-gated short/long choice stays in
    analysis_loop(), not here -- see module docstring)."""

    def __init__(self, max_notes, min_mag_ratio, harmonic_tolerance_cents, max_peak_candidates,
                 harmonic_max_number, min_freq_hz, max_freq_hz):
        self.max_notes = max_notes
        self.min_mag_ratio = min_mag_ratio
        self.harmonic_tolerance_cents = harmonic_tolerance_cents
        self.max_peak_candidates = max_peak_candidates
        self.harmonic_max_number = harmonic_max_number
        self.min_freq_hz = min_freq_hz
        self.max_freq_hz = max_freq_hz

    def detect(self, window, sample_rate):
        return multipitch.detect(
            window,
            sample_rate,
            max_notes=self.max_notes,
            min_mag_ratio=self.min_mag_ratio,
            harmonic_tolerance_cents=self.harmonic_tolerance_cents,
            max_peak_candidates=self.max_peak_candidates,
            harmonic_max_number=self.harmonic_max_number,
            min_freq_hz=self.min_freq_hz,
            max_freq_hz=self.max_freq_hz,
        )


def default_pitch_backend(config):
    """Builds a YinBackend from config.* exactly as analysis_loop() called
    detect_pitch() directly before this seam existed -- SessionState's
    default, so nothing about default behavior changes."""
    return YinBackend(
        config.FMIN, config.FMAX, config.YIN_THRESHOLD,
        config.YIN_SUBHARMONIC_MAX_MULTIPLE, config.YIN_SUBHARMONIC_MARGIN, config.YIN_SUBHARMONIC_SKIP_CMND,
    )


def default_poly_backend(config):
    """Builds a SpectralPeakBackend from config.* exactly as analysis_loop()
    called multipitch.detect() directly before this seam existed --
    SessionState's default, so nothing about default behavior changes."""
    return SpectralPeakBackend(
        config.CHORD_MAX_NOTES, config.CHORD_PEAK_MIN_MAG_RATIO, config.CHORD_HARMONIC_TOLERANCE_CENTS,
        config.CHORD_MAX_PEAK_CANDIDATES, config.CHORD_HARMONIC_MAX_NUMBER, config.FMIN, config.FMAX,
    )
