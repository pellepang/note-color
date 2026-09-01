"""DetectionBackend protocol demo (docs/research/architecture-modernization-plan.md
S3.1) -- a typing.Protocol capturing the exact shape main.analysis_loop()
calls pitch_detect.detect_pitch() with today, plus two concrete backends:

  - YinBackend    -- zero-behavior-change wrapper around the real,
                     unmodified pitch_detect.detect_pitch().
  - PyinLiteBackend -- a genuinely different algorithm: a lightweight
                     pYIN-style backend (Mauch & Dixon 2014) that sweeps
                     many YIN thresholds over the SAME shared CMND curve
                     (built from pitch_detect.compute_spectrum()'s FFT,
                     reusing pitch_detect's own private difference-function/
                     CMND helpers rather than reimplementing YIN's math) and
                     turns "how many thresholds pick this candidate" into a
                     genuine voicing PROBABILITY, instead of YIN's single
                     hard threshold + first-crossing pick reported as
                     `1 - cmnd[tau]`.

This file imports pitch_detect but never edits it -- see the repo's
prototype convention (prototypes/issue-42-menu-animation/).
"""

import os
import sys
from typing import Optional, Protocol, Tuple

import numpy as np

# Make the real repo modules importable when this script is run directly
# via `.venv/bin/python prototypes/detection-backend-protocol/*.py` --
# sys.path[0] is this file's own directory, not the repo root.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import config  # noqa: E402
import pitch_detect  # noqa: E402


class MonoPitchBackend(Protocol):
    """Mirrors pitch_detect.detect_pitch()'s call shape at its one real
    call site, main.analysis_loop() (main.py:329-332):

        freq, confidence = detect_pitch(
            ring, config.SAMPLE_RATE, spectrum, config.FMIN, config.FMAX,
            config.YIN_THRESHOLD, config.YIN_SUBHARMONIC_MAX_MULTIPLE,
            config.YIN_SUBHARMONIC_MARGIN, config.YIN_SUBHARMONIC_SKIP_CMND,
        )

    Per architecture-modernization-plan.md S3.1, algorithm-specific tuning
    knobs (fmin/fmax/threshold/subharmonic_*) move into each backend's own
    __init__ rather than staying per-call arguments -- that's the actual
    pluggability win: analysis_loop() no longer needs to know which of
    config.py's ~90 constants apply to whichever backend is active.
    `sample_rate` stays a per-call argument since it's a session-wide
    constant every backend genuinely needs, not an algorithm-specific knob.
    """

    def detect(self, ring: np.ndarray, spectrum: np.ndarray, sample_rate: int) -> Tuple[Optional[float], float]:
        """-> (freq_hz or None, confidence/voicing-probability in [0, 1]).
        Return shape matches pitch_detect.detect_pitch()'s (freq, confidence)
        exactly -- this is the shape note_smoother.NoteSmoother.update()
        already consumes, unchanged, per the architecture doc's finding
        that the stabilization layer is already backend-agnostic."""
        ...


class YinBackend:
    """Wraps the real, unmodified pitch_detect.detect_pitch() -- zero
    behavior change versus main.py's current direct call. Config knobs are
    captured once at construction (defaulting to today's exact config.py
    values), not threaded through every detect() call."""

    def __init__(
        self,
        fmin=config.FMIN,
        fmax=config.FMAX,
        threshold=config.YIN_THRESHOLD,
        subharmonic_max_multiple=config.YIN_SUBHARMONIC_MAX_MULTIPLE,
        subharmonic_margin=config.YIN_SUBHARMONIC_MARGIN,
        subharmonic_skip_cmnd=config.YIN_SUBHARMONIC_SKIP_CMND,
    ):
        self.fmin = fmin
        self.fmax = fmax
        self.threshold = threshold
        self.subharmonic_max_multiple = subharmonic_max_multiple
        self.subharmonic_margin = subharmonic_margin
        self.subharmonic_skip_cmnd = subharmonic_skip_cmnd

    def detect(self, ring, spectrum, sample_rate):
        # Straight call into the real, unmodified function -- no
        # reimplementation, no behavior drift.
        return pitch_detect.detect_pitch(
            ring, sample_rate, spectrum, self.fmin, self.fmax, self.threshold,
            self.subharmonic_max_multiple, self.subharmonic_margin, self.subharmonic_skip_cmnd,
        )


def _scan_tau_for_threshold(cmnd, tau_min, tau_max, threshold):
    """Same ascending-scan-then-descend-to-local-min logic as
    pitch_detect.detect_pitch()'s primary scan (pitch_detect.py:88-96),
    factored out so PyinLiteBackend can run it once per swept threshold.
    No subharmonic correction here -- pYIN's own multi-candidate
    probability weighting is a different mechanism for the same
    "which octave is this really" problem YIN's subharmonic check solves
    with a single deterministic correction; running both would conflate
    what each is actually demonstrating."""
    t = tau_min
    while t < tau_max:
        if cmnd[t] < threshold:
            while t + 1 < tau_max and cmnd[t + 1] < cmnd[t]:
                t += 1
            return t
        t += 1
    return None


class PyinLiteBackend:
    """A lightweight pYIN-style probabilistic backend (Mauch & Dixon,
    ISMIR 2014), reusing pitch_detect.compute_spectrum()'s shared FFT (via
    the `spectrum` argument every backend receives) and pitch_detect's own
    private CMND helpers (`_difference_function`/`_cmndf`/`_parabolic_vertex`)
    -- NOT a reimplementation of YIN's core math, just a different decision
    rule layered on top of the same difference function.

    Real pYIN's core idea: instead of committing to ONE hard threshold and
    stopping at the first tau that clears it (classic YIN, and this app's
    detect_pitch()), sweep MANY thresholds across the CMND curve. Each
    threshold's ascending-scan-then-descend pick "votes" for whichever tau
    it selects (or for "unvoiced" if none clears it). The fraction of
    thresholds voting for a given tau becomes that candidate's probability
    mass -- turning what YIN reports as a single ad-hoc `1 - cmnd[tau]`
    "confidence" into an actual voicing probability with real multi-
    candidate structure (useful exactly where YIN's octave-doubling
    history lives: a shallow near-threshold dip at a short lag competing
    against a deeper dip at the true, longer fundamental lag will show up
    here as split probability mass between two real candidates, not a
    single silently-wrong pick).

    Cost note (see README): this prototype's threshold sweep is a plain
    Python loop over `n_thresholds` full ascending scans -- O(n_thresholds
    * tau_max) versus detect_pitch()'s O(tau_max) single scan. Not
    optimized for a live per-hop budget; see README's Known limitations.
    """

    def __init__(
        self,
        fmin=config.FMIN,
        fmax=config.FMAX,
        n_thresholds=100,
        threshold_min=0.01,
        threshold_max=1.0,
    ):
        self.fmin = fmin
        self.fmax = fmax
        self.thresholds = np.linspace(threshold_min, threshold_max, n_thresholds)

    def detect(self, ring, spectrum, sample_rate):
        x = np.asarray(ring, dtype=np.float64)
        w = len(x)
        tau_min = max(1, int(sample_rate / self.fmax))
        tau_max = min(w - 1, int(sample_rate / self.fmin))
        if tau_max <= tau_min:
            return None, 0.0

        d = pitch_detect._difference_function(x, spectrum, tau_max + 1)
        cmnd = pitch_detect._cmndf(d)

        votes = {}
        unvoiced_votes = 0
        for theta in self.thresholds:
            tau = _scan_tau_for_threshold(cmnd, tau_min, tau_max, theta)
            if tau is None:
                unvoiced_votes += 1
            else:
                votes[tau] = votes.get(tau, 0) + 1

        total = len(self.thresholds)
        if not votes:
            return None, 0.0

        best_tau = max(votes, key=votes.get)
        voicing_probability = 1.0 - unvoiced_votes / total

        shift, _ = pitch_detect._parabolic_vertex(cmnd, best_tau)
        tau_refined = best_tau + shift
        if tau_refined <= 0:
            return None, 0.0

        freq = sample_rate / tau_refined
        return float(freq), float(max(0.0, min(1.0, voicing_probability)))

    def candidate_distribution(self, ring, spectrum, sample_rate):
        """Extra diagnostic (not part of the MonoPitchBackend protocol):
        the full per-tau vote distribution, for the harness to print and
        show the multi-candidate structure YIN's single-pick interface
        can't expose at all."""
        x = np.asarray(ring, dtype=np.float64)
        w = len(x)
        tau_min = max(1, int(sample_rate / self.fmax))
        tau_max = min(w - 1, int(sample_rate / self.fmin))
        if tau_max <= tau_min:
            return {}
        d = pitch_detect._difference_function(x, spectrum, tau_max + 1)
        cmnd = pitch_detect._cmndf(d)
        votes = {}
        for theta in self.thresholds:
            tau = _scan_tau_for_threshold(cmnd, tau_min, tau_max, theta)
            if tau is not None:
                votes[tau] = votes.get(tau, 0) + 1
        total = len(self.thresholds)
        return {
            float(sample_rate / tau): count / total
            for tau, count in sorted(votes.items(), key=lambda kv: kv[1], reverse=True)
        }
