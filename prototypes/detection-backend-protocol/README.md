# Prototype: `DetectionBackend` protocol

Demonstrates `docs/research/architecture-modernization-plan.md` S3.1's
proposed `DetectionBackend` protocol: a `typing.Protocol` capturing the
exact shape `main.analysis_loop()` calls `pitch_detect.detect_pitch()`
with today, plus two concrete backends behind it — one a zero-behavior-
change wrapper around the real YIN implementation, one a genuinely
different algorithm (a lightweight pYIN-style probabilistic backend).

Standalone, self-contained, does not modify any file outside this
directory — follows the same convention as
`prototypes/issue-42-menu-animation/`.

## Files

- `backends.py` — `MonoPitchBackend` protocol, `YinBackend` (thin wrapper
  around `pitch_detect.detect_pitch()`, unmodified), `PyinLiteBackend` (new
  algorithm, built on `pitch_detect.compute_spectrum()`'s shared FFT and
  `pitch_detect`'s own private CMND helpers).
- `harness.py` — runs both backends against three synthesized tones and
  prints a side-by-side comparison table.

## How to run

```
cd ~/note-color
.venv/bin/python prototypes/detection-backend-protocol/harness.py
```

## What it actually produced (real output, not hypothetical)

```
case                                                                   backend       freq_hz  cents_off  conf/voice_p
----------------------------------------------------------------------------------------------------------------------
A4, plain sine (440Hz)                                                 YIN            440.08         +0         1.000
A4, plain sine (440Hz)                                                 pYIN-lite      440.08         +0         1.000
                                                                       (pYIN-lite candidates) 441.0Hz=1.00

G#2, harmonic-rich fundamental-dominant (92.5Hz, issue #69 profile)    YIN             92.50         +0         1.000
G#2, harmonic-rich fundamental-dominant (92.5Hz, issue #69 profile)    pYIN-lite       92.50         +0         1.000
                                                                       (pYIN-lite candidates) 92.6Hz=1.00

C2, adversarial weak-fundamental/3rd-harmonic-dominant (65.41Hz, issue #69 regression profile) YIN             65.41         +0         1.000
C2, adversarial weak-fundamental/3rd-harmonic-dominant (65.41Hz, issue #69 regression profile) pYIN-lite      197.69      +1915         1.000
                                                                       (pYIN-lite candidates) 196.9Hz=0.93, 65.4Hz=0.06, 97.6Hz=0.01
```

Both backends land correctly on the plain tone and the ordinary
harmonic-rich low tone. On the third, adversarial case — the exact profile
`tests/test_pitch_detect.py::test_octave2_silent_fundamental_dominant_3rd_harmonic_not_octave_doubled`
uses to prove YIN's issue #69 subharmonic-correction fix — `YinBackend`
gets it right *because* that correction is active by default (it's the
same `pitch_detect.detect_pitch()` call, config defaults unchanged).
`PyinLiteBackend` has **no equivalent correction** and locks onto the 3rd
harmonic (197.69Hz ≈ 3×65.41Hz), reported with a deceptively confident
`voice_p=1.000` — but its `candidate_distribution()` diagnostic (not part
of the `MonoPitchBackend` protocol itself, an extra method) shows the
underlying ambiguity honestly: 93% of the swept thresholds voted for the
wrong (3rd-harmonic) candidate, 6% for the correct fundamental, 1% for a
third option. This is real, run output — not massaged to make either
backend look better. See "Honest downside" below for what this means.

## System architecture reasoning

**Which real files/call-sites would change.** Per
`docs/research/architecture-modernization-plan.md` S3.1 (and confirmed by
reading the actual code, not just the doc's paraphrase):

- `main.py:329-332` — the real call site:
  ```python
  freq, confidence = detect_pitch(
      ring, config.SAMPLE_RATE, spectrum, config.FMIN, config.FMAX, config.YIN_THRESHOLD,
      config.YIN_SUBHARMONIC_MAX_MULTIPLE, config.YIN_SUBHARMONIC_MARGIN, config.YIN_SUBHARMONIC_SKIP_CMND,
  )
  ```
  would become `pitch_backend.detect(ring, spectrum, config.SAMPLE_RATE)` —
  one line, same call site, all six algorithm-specific config constants
  move into whichever backend object was constructed (matches this
  prototype's `YinBackend.__init__`/`PyinLiteBackend.__init__` exactly).
- `main.analysis_loop()`'s signature (`main.py:299`) gains a
  `pitch_backend` parameter.
- `main.SessionState.__init__` (`main.py:1166` per the architecture doc;
  not re-verified line-by-line here since this prototype never touches
  `main.py`) gains an optional `pitch_backend=None` param defaulting to
  `YinBackend(...)` built from today's `config.*` values — so adopting
  this changes **zero** default behavior.
- `note_smoother.NoteSmoother.update()` (the actual consumer of
  `(freq, confidence)`) needs **no change at all** — this prototype's own
  harness output above is proof: both backends return the exact
  `(Optional[float], float)` shape `NoteSmoother` already consumes,
  confirming the architecture doc's claim that "the stabilization layer is
  already backend-agnostic."

**Size of the change.** Small. New file (`detection_backends.py` in the
real repo, ~80-120 lines per the doc's estimate — this prototype's
`backends.py` is 210 lines, but includes extensive docstrings and the
extra `candidate_distribution()` diagnostic not needed for adoption).
Everything else is the two-line call-site edit plus one new constructor
parameter. No change to `pitch_detect.py`, `multipitch.py`,
`note_smoother.py`, or any display module.

**What it unlocks long-term.** Once `analysis_loop()` calls through
`pitch_backend.detect(...)` instead of naming `detect_pitch` directly,
adding a third backend (e.g. a desktop-tier SwiftF0 neural backend per
`docs/research/detection-systems-survey.md`'s long-term recommendation 6)
is purely additive — a new class implementing the same three-argument
`detect()` method, selected at `SessionState` construction time, with zero
further edits to `analysis_loop()`'s control flow. That's the concrete
payoff this prototype demonstrates isn't hypothetical: `harness.py` above
already runs two structurally different algorithms — one calling straight
into unmodified production code, one a from-scratch probabilistic
method — through the identical `.detect(ring, spectrum, sample_rate)`
call, with the harness itself never branching on which backend it's
talking to.

## Honest downside / risk

1. **`PyinLiteBackend` is not a drop-in accuracy win — it's a different
   algorithm with its own failure mode**, demonstrated above, not
   hypothesized: on the exact adversarial low-register profile this
   project's own test suite uses to validate YIN's octave-doubling fix,
   pYIN-lite (as implemented here, with no equivalent subharmonic
   correction) gets the octave wrong while reporting maximal confidence.
   Adopting a second real backend is not free of the same "provisional,
   not yet field-tuned" burden every algorithm choice in this codebase
   already carries per `docs/DECISIONS.md` — a hypothetical production
   pYIN backend would need its own version of issue #69's fix, not
   inherit YIN's for free, since the two algorithms pick candidates by
   entirely different mechanisms (single hard threshold + first-crossing
   vs. multi-threshold voting).
2. **Performance: this prototype's threshold sweep is unoptimized and
   not Pi-feasible as written.** `PyinLiteBackend.detect()` runs a plain
   Python loop over `n_thresholds=100` full ascending CMND scans —
   O(100 × tau_max) versus `detect_pitch()`'s single O(tau_max) scan. This
   was not benchmarked against a real per-hop latency budget (the
   architecture/survey docs' own stated target is comfortably under
   150ms end-to-end) — it would need vectorizing (the threshold sweep is
   embarrassingly parallel across thresholds, a real NumPy-vectorization
   opportunity) before it could be considered for the live path at all.
   This prototype exists to prove the *interface* is pluggable, not that
   pYIN-lite specifically is ready to ship.
3. **The Protocol itself is a real interface, but a thin one.** Per the
   architecture doc's own risk callout, the temptation to pad
   `MonoPitchBackend` with YIN-specific parameters (e.g. a
   `confidence_threshold` argument) should be resisted until a second real
   production backend exists to design against — this prototype's
   `PyinLiteBackend` deliberately takes its own different parameter set
   (`n_thresholds`, `threshold_min/max`) entirely in `__init__`, never in
   the shared `detect()` signature, to prove that discipline holds even
   for an algorithm with a very different internal shape than YIN's.
