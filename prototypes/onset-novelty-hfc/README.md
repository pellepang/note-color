# Prototype: HFC / complex-domain onset novelty

Demonstrates `docs/research/detection-systems-survey.md` S3's near-term
recommendation 1: port aubio-style High-Frequency Content (HFC) and
complex-domain onset novelty into pure NumPy functions matching
`onset_detect.py`'s existing style, and compare them against the app's
real `spectral_flux()` on synthesized note sequences with known onset
times.

Standalone, self-contained, does not modify `onset_detect.py` or any other
existing file — follows the `prototypes/issue-42-menu-animation/`
convention.

## Files

- `novelty.py` — `hfc_novelty(spectrum, prev_spectrum)` and
  `complex_domain_novelty(spectrum, prev_spectrum, prev_prev_spectrum)`,
  pure and `None`-safe, matching `onset_detect.spectral_flux()`'s exact
  style (including its self-relative normalization convention — see below).
- `harness.py` — synthesizes a short melody with known onset times, runs
  a real `config.WINDOW_SIZE`/`config.BLOCK_SIZE` hop loop (same shape as
  `main.analysis_loop()`) computing all three novelty measures every hop,
  peak-picks each with an adaptive median+MAD threshold (independently
  swept per method), and reports hits/misses/false-positives/timing error
  against ground truth.

## How to run

```
cd ~/note-color
.venv/bin/python prototypes/onset-novelty-hfc/harness.py
```

## What it actually produced (real output)

Test sequence: silence → A3 (harmonic-rich) → **legato transition, no
gap** → E4 (harmonic-rich) → silence → A2 (low-register, harmonic-rich,
issue #69's register) → silence → A4 (plain sine, **quiet** attack,
amplitude 0.08 vs. the others' 0.25-0.30). Ground truth onsets at 0.150s,
0.550s (the legato transition), 1.070s, 1.570s (the quiet attack).

```
method           best_k_mad  threshold  hits  misses  false_pos  mean_err_ms
------------------------------------------------------------------------------------------
spectral_flux             5     0.4817     3       1          0         22.0
                 detected onsets (s): ['0.163', '1.091', '1.602']

hfc_novelty               3     0.0073     4       0         13         27.6
                 detected onsets (s): ['0.163', '0.279', '0.395', ... 17 total]

complex_domain            3     1.2399     3       1          0         14.3
                 detected onsets (s): ['0.163', '1.091', '1.579']
```

(Each method's peak-picking threshold multiplier `k_mad` was swept
independently over `{3,4,5,6,8,10,12,15,20,25,30,40}` and the best-scoring
setting reported per method — see `harness.py`'s
`best_over_k_mad()` docstring for why a single shared threshold would have
unfairly penalized whichever measure has a different noise character,
mirroring how `config.ONSET_FLUX_THRESHOLD` is itself its own hand-tuned
constant, not shared with anything else.)

**Three concrete, real findings, not hypothesized:**

1. **All three methods missed the same onset** — the legato transition at
   0.550s (A3→E4, no silence gap, both harmonic-rich, similar loudness).
   Neither magnitude-difference (`spectral_flux`), frequency-weighted
   magnitude-difference (`hfc_novelty`), nor phase-prediction-deviation
   (`complex_domain`) caught it at their individually best-tuned
   thresholds. This corroborates something already implicit in this
   project's own design: `note_smoother.py:105`'s onset gate is `note-
   change OR RMS jump OR spectral_flux() clearing threshold` — three
   separate signals, not spectral novelty alone — because a legato
   transition genuinely can lack a strong spectral novelty spike on its
   own. A pure novelty-function swap, however good, would not by itself
   fix legato-transition detection; the multi-signal gate structure
   already in `note_smoother.py` is doing real work here.
2. **`complex_domain_novelty` found the quiet attack (1.570s) with
   noticeably better timing precision** — 14.3ms mean error across its 3
   hits vs. `spectral_flux`'s 22.0ms across its 3 hits — plausibly because
   phase deviates from linear prediction immediately at a genuine attack,
   while magnitude has to actually rise measurably first. This is a
   real, if small-sample (3 hits each), signal in favor of the aubio
   `complex` ODF's reported literature edge (0.700 vs. plain spectral
   difference's 0.647-0.672, cited in
   `docs/research/detection-systems-survey.md`'s comparison table).
3. **`hfc_novelty` as implemented is unusably noisy on sustained tones at
   any threshold that also catches all 4 true onsets** — its best-scoring
   setting (`k_mad=3`, the *loosest* tested) still produced 13 false
   positives clustered at roughly one per 100-120ms during every
   sustained note, not just at attacks. Bin-index weighting amplifies
   whatever small hop-to-hop wobble exists in high-frequency bins (the
   same unwindowed-spectrum-sidelobe wobble `onset_detect.py`'s own
   `spectral_flux()` docstring already documents fixing for issue #66),
   and that wobble is itself weighted proportionally to bin index — so a
   perfectly sustained tone's steady-state noise floor gets amplified
   into spurious novelty far more than in the flat-weighted
   `spectral_flux()`. See "Honest downside" below — this is not simply
   "needs a different threshold," it needs either extra smoothing or
   normalization this prototype's straightforward port doesn't have.

## System architecture reasoning

**Which real files/call-sites would change.** Per
`docs/research/detection-systems-survey.md` S3 recommendation 1 and this
project's own `onset_detect.py`/`note_smoother.py`/`tempo_tracker.py`:

- `onset_detect.py` would gain `hfc_novelty()`/`complex_domain_novelty()`
  as new functions, same file, same style as the existing
  `spectral_flux()`/`chroma_flux()` (lines 12-70 and 73-86 respectively) —
  this prototype's `novelty.py` is written to be near-verbatim-portable
  into that file (same signature conventions, same `None`-safety
  contract, same docstring style).
- `note_smoother.py:105` — the mono onset gate's actual call site:
  ```python
  elif spectral_flux(spectrum, self.prev_spectrum) >= self.onset_flux_threshold:
  ```
  Swapping in `hfc_novelty` here is a one-line change *if* it were
  production-ready (see finding 3 above — it isn't, as implemented). Using
  `complex_domain_novelty` here is a **larger** change: `NoteSmoother`
  currently tracks only `self.prev_spectrum` (`note_smoother.py:32,62,110`)
  — `complex_domain_novelty` needs the *previous two* frames, so
  `NoteSmoother` would need a second piece of rolling state
  (`self.prev_prev_spectrum`). This is a real, concrete integration cost
  this prototype's harness had to solve too (`harness.py`'s hop loop
  explicitly carries both `prev_spectrum` and `prev_prev_spectrum`) — not
  a hypothetical one.
- `main.py:357-359` — the tempo-tracking call site:
  ```python
  chroma_novelty = chroma_flux(main_chroma, prev_chroma)
  bpm_estimate = tempo_tracker.update(chroma_novelty)
  ```
  A chroma-domain HFC/complex-domain analog would need its own
  12-bin-vector version of these functions (this prototype only builds
  the full-spectrum versions, matching `spectral_flux()`; `chroma_flux()`'s
  12-bin equivalents are a separate, smaller port, not built here).

**Size of the change.** Small for `hfc_novelty` (same 2-frame state
`spectral_flux()` already uses, direct drop-in). Small-to-medium for
`complex_domain_novelty` specifically because of the 3-frame state
requirement — `NoteSmoother.__init__`/`.update()` both need a second
tracked attribute, not just a new function call.

**What it unlocks long-term.** Per the survey doc, this is explicitly
staged as "worth doing now or soon, near-zero integration cost" — the
real payoff isn't a big architectural unlock like the `DetectionBackend`
prototype's, it's a candidate accuracy improvement for a piece of the
pipeline (`note_smoother.py`'s onset gate, `tempo_tracker.py`'s beat
detection) that's already documented as provisional and not yet tuned
against real playing (`CLAUDE.md`'s Known limitations: "`ONSET_FLUX_
THRESHOLD`... likewise provisional"). If `complex_domain_novelty`'s timing
precision advantage (finding 2) holds up on real, non-synthetic audio, it
could feed directly into issue #70's known "short note's own 20ms attack
fade... straddle a hop boundary" limitation, since better timing precision
at the actual attack instant is exactly what that limitation needs.

## Real `--source loopback` validation (closes downside 3 below)

`real_loopback_validation.py` (added for issue #78's resolution) reruns
this exact comparison over a REAL PortAudio round trip -- the same
muted-unattended `--source loopback` methodology
`scripts/acoustic_pipeline_test.py` itself uses -- instead of pure
in-memory synthetic arrays. Same 4-onset note sequence, but captured
through the actual audio stack (real resampling, real timing jitter, a
real ring buffer fed by `AudioCapture`), computing all three novelty
measures per real hop straight off `compute_spectrum(ring)`.

```
.venv/bin/python prototypes/onset-novelty-hfc/real_loopback_validation.py
```

**Result at production's actual fixed threshold (`config.ONSET_FLUX_THRESHOLD
= 0.3`, not an adaptively-swept one)** -- the realistic comparison, since
that's how the app actually decides an onset, not via a per-run MAD sweep:

```
method             threshold  hits  misses  false_pos  mean_err_ms
spectral_flux (current)  0.30     4       0          5         61.0
hfc                      0.30     4       0         16         79.7
complex_domain           0.30     4       0         22         84.4
```

**This reverses the synthetic harness's apparent `complex_domain` timing
advantage.** On real captured audio, at the exact threshold production
uses, both `hfc` and `complex_domain` are substantially noisier than the
currently-shipped `spectral_flux()` -- 3.2x and 4.4x more false positives
respectively, not fewer. This is exactly the "synthetic/loopback-clean
result doesn't survive real testing" pattern this project has already hit
twice (issues #69, #71) -- reported plainly rather than cherry-picking the
earlier synthetic result. `spectral_flux()` itself still shows 5 false
positives even on this idealized muted round trip (no physical mic
coloration) -- a known, already-documented limitation (issue #70's
real-audio-timing-jitter case), not new.

**Conclusion: do not wire `hfc_novelty()`/`complex_domain_novelty()` into
`note_smoother.py`'s onset gate.** Both are ported into `onset_detect.py`
(matching this prototype's `novelty.py` near-verbatim) as tested,
available-but-unused functions -- issue #78's own scope explicitly allows
"not worth the churn" as a valid outcome, and the real-hardware evidence
now points that way for both candidates, not just `hfc` alone.

## Honest downside / risk

1. **`hfc_novelty` is not production-ready as implemented** (finding 3) —
   it would need either a smoothing stage (e.g. a short moving-average or
   median filter over the raw HFC curve before differencing, common in
   real HFC-based onset detectors) or a different normalization before it
   could plausibly replace or augment `spectral_flux()` at
   `note_smoother.py:105`. Reporting this as a real finding, not
   glossing over it, is the point of "actually run it."
2. **`complex_domain_novelty`'s 3-frame state requirement is a genuine,
   non-trivial integration cost**, not a paper cut — every caller
   (`NoteSmoother`, and a hypothetical chroma-domain tempo-tracker analog)
   needs a second rolling-state field, and the first two hops of any
   session/re-attack have no signal at all (`None`-safe fallback to 0.0,
   same convention as everything else in `onset_detect.py`, but a real
   "blind for 2 hops" window nonetheless — ~46ms at this app's hop rate,
   probably negligible but not measured here).
3. **The comparison harness's ground truth has exactly 4 onsets** — small
   enough that any one hit/miss swings the reported numbers by 25
   percentage points. The `complex_domain` vs. `spectral_flux` timing-
   precision gap (14.3ms vs. 22.0ms, finding 2) is a real measurement from
   this run, but with an n of 3 matched onsets each, it should be read as
   "a real, reproducible signal on this synthetic test," not as a
   statistically robust claim generalizable to real playing — the
   project's own repeated caveat about synthetic/loopback-clean findings
   not always surviving real-mic testing (issues #69, #71) applies here
   too, and this prototype was not tested against
   `scripts/acoustic_pipeline_test.py`'s real-loopback suites at all.
4. **The adaptive median+MAD threshold with per-method sweeping is a
   fair *comparison* methodology, not a proposal for how the real app
   should pick thresholds** — `config.ONSET_FLUX_THRESHOLD` is a fixed
   constant chosen once, not adaptively recomputed over a rolling window
   every session; this harness's adaptive approach was necessary only to
   compare three differently-scaled measures fairly within one short
   synthetic test run, and reusing it live would be a different, unproven
   design decision on its own.
