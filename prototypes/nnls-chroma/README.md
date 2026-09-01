# Prototype: NNLS-based approximate transcription ahead of chroma folding

Demonstrates `docs/research/detection-systems-survey.md` §3's near-term
recommendation 3 (issue #81): prototype Mauch & Dixon's NNLS-Chroma
approach — a per-frame non-negative-least-squares solve against a
harmonic-profile note dictionary — as a replacement input to
`chord_templates.match()`, ahead of `chroma.fold()`'s existing linear
Gaussian-weighting projection.

Standalone, self-contained, does not modify `chroma.py`/`chord_templates.py`
or any other existing file — follows the `prototypes/detection-backend-
protocol/`/`prototypes/onset-novelty-hfc/` convention.

## Files

- `nnls_chroma.py` — `nnls_chroma(spectrum, sample_rate)`: builds a
  per-real-note (MIDI 24-96, not just 12 pitch classes) harmonic-weighted
  dictionary matrix (same Gaussian log-frequency profile
  `chroma._weighting_matrix()` already uses per pitch class, just not yet
  folded across octaves), solves `scipy.optimize.nnls(D, magnitude)` for
  nonnegative per-note activations, then sums activations by pitch class
  into the same 12-element chroma-vector shape `chroma.fold()` returns —
  a drop-in-comparable output for `chord_templates.match()`.
- `harness.py` — timing comparison (`chroma.fold()` vs. `nnls_chroma()`,
  many trials over a real `config.WINDOW_SIZE` spectrum) and
  chord-recognition-accuracy comparison (both chroma methods fed into the
  REAL, unmodified `chord_templates.match()` — clean chords, a dense
  6-note voicing, and this project's own documented harmonic-near-miss
  case: a root + a fifth-plus-octave above it, ~2 cents from the root's
  own 3rd harmonic).

## How to run

```
cd ~/note-color
.venv/bin/python prototypes/nnls-chroma/harness.py
```

## What it actually produced (real output, this dev machine)

```
== Per-hop cost (desktop-class, NOT Pi-measured) ==
chroma.fold():  0.011 ms/hop  (200 trials)
nnls_chroma():  9.817 ms/hop  (200 trials)
ratio: 932.0x slower than the current linear fold
(hop budget: config.BLOCK_SIZE/config.SAMPLE_RATE = 23.2 ms)

== Chord-recognition accuracy (both -> REAL chord_templates.match()) ==
case                          expected  fold()                nnls()
C major triad                 C         C (sim 0.908)          C (sim 0.935)
A minor triad                 A-        A- (sim 0.901)         A- (sim 0.937)
G dominant 7                  G7        G11 (sim 0.885)  WRONG G7 (sim 0.934)  CORRECT
D major 7                     DΔ7       DΔ7 (sim 0.933)        DΔ7 (sim 0.946)
dense 6-note (no clean answer)  --      D13 (sim 0.802)        C9 (sim 0.885)

== Harmonic-near-miss case (root + fifth-an-octave-up, ~2 cents from
   root's own 3rd harmonic -- this project's own documented open limitation) ==
expected: C major triad (pcs=[0, 4, 7])
fold():   C (sim 0.854), top-3 pcs by weight: [7, 0, 4]
nnls():   C (sim 0.871), top-3 pcs by weight: [4, 0, 7]
```

## Three concrete, real findings

1. **Real, measured accuracy win on a documented failure class.** The G
   dominant-7 case is where this actually mattered: `chroma.fold()` +
   the real `chord_templates.match()` over-called it as `G11` (a real,
   already-documented failure mode this project has hit before — issue
   #56, "Chord-name recognition over-calls extended/slash chords under
   realistic harmonic-rich tones"). `nnls_chroma()` fed into the exact
   same matcher got it right: `G7`. This is a genuine, reproducible
   instance of NNLS suppressing spurious chroma-bin pollution from
   misfolded harmonics — exactly the mechanism Mauch & Dixon's paper
   credits for their measured +12pp result, not a coincidence.
2. **The specific harmonic-near-miss case this was nominally scoped
   against did NOT show a decisive win.** Both methods correctly named
   the chord `C` with the right top-3 pitch-class set; nnls's similarity
   score was marginally higher (0.871 vs. 0.854) but the two methods'
   *internal* pitch-class ranking actually differed (fold ranks the
   colliding 5th above the root; nnls ranks the 3rd above the root) —
   neither is unambiguously "more correct" here, and this single test
   doesn't demonstrate NNLS dissolving the exact-coincidence problem.
   This matches `detection-systems-survey.md`'s own explicit caveat,
   written *before* this prototype ran: NNLS is "also still single-hop,"
   and there's "no strong a priori reason to expect it dissolves the
   identical spectral-identity problem" for an exact 3:1 collision —
   confirmed here, not just theorized.
3. **The timing cost is severe and did not respond well to the one
   obvious mitigation tried.** 932x slower than the current linear fold
   at full range (MIDI 24-96, 73 candidate notes); narrowing the
   dictionary to `config.FMIN`-`FMAX`'s actual working range (MIDI 36-83,
   48 notes) only brought it down to 6.24ms/hop — still **566x** slower.
   9.8ms (or 6.2ms) alone, on a fast modern desktop CPU, is already 27-42%
   of this app's entire 23.2ms hop budget (`config.BLOCK_SIZE /
   config.SAMPLE_RATE`) for JUST the chroma-folding step — before YIN,
   multipitch peak-picking, chord matching, onset/tempo tracking, or
   rendering. CLAUDE.md's own measured number for chord mode's ENTIRE
   pipeline on a real Pi Zero 2 W is ~3ms/hop worst case — this
   prototype's chroma step alone, on a much faster desktop, already
   exceeds that. The bottleneck is the NNLS solve itself (`scipy.
   optimize.nnls`'s active-set iteration), not dictionary construction
   (cached) or dictionary size dominating the cost.

## Verdict

**Not viable for the live path as implemented — do not adopt for
`chroma.fold()`'s live call sites.** The accuracy signal is real (finding
1) and worth keeping in mind, but the timing cost (finding 3) is
disqualifying by a wide margin on this project's own stated non-negotiable
latency budget, and this is *before* accounting for Pi Zero 2 W typically
running meaningfully slower than desktop for this class of iterative
numerical workload (unmeasured here — no Pi hardware in this session, see
Known limitations below).

**Worth a narrower follow-up: batch-only use.** `virtualnote transcribe`
has no live-per-hop latency constraint — the same reasoning that already
justifies `librosa`/`music21` being isolated to offline-only modules
(`batch_transcribe.py`, `score_writer.py`) applies here. A future ticket
could prototype NNLS chroma as an optional, non-default chroma source for
`batch_transcribe.transcribe()` specifically, where 6-10ms/hop is
completely affordable — this is a smaller, more honestly-scoped follow-up
than "adopt for chroma.fold() generally," and is NOT built here (out of
this prototype's scope, which was issue #81's live-chroma-folding
question specifically).

## Honest downside / risk

1. **No Pi-class hardware timing exists for this prototype.** The 932x/
   566x ratios are desktop-relative; if Pi Zero 2 W's iterative-solver
   performance is *disproportionately* worse than its linear-algebra
   performance (plausible — NNLS's active-set method has data-dependent
   iteration count, unlike a fixed-cost matmul), the real-hardware gap
   could be even larger than the desktop ratio suggests. Given the
   desktop number alone already exceeds this app's real Pi chord-pipeline
   budget, this wasn't pursued further.
2. **The accuracy comparison used only 5 clean/synthetic chord cases plus
   1 near-miss case** — not run against `scripts/acoustic_pipeline_test.py`'s
   real `chords`/`density` acoustic suites (issue #81's own scope asked
   for this if feasible; not done here given the timing verdict already
   disqualifies the live-path use case this comparison would have
   informed). A batch-only follow-up (see Verdict) would need that fuller
   validation before any real adoption call there either.
3. **The dictionary (`_note_dictionary()`) reuses `chroma.py`'s own
   Gaussian-profile math verbatim**, just per-note instead of pre-summed
   to pitch class — this is a faithful, direct comparison (isolates the
   folding *mechanism* — linear projection vs. constrained least-squares —
   as the only variable), but a "real" NNLS-chroma implementation per
   Mauch & Dixon's paper might use a different harmonic profile shape
   (their paper's own tuned parameters) that this prototype doesn't
   attempt to reproduce exactly.
