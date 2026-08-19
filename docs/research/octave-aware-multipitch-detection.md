# Octave-aware multi-pitch note detection (research for chord mode)

Ticket: [pellepang/note-color#10](https://github.com/pellepang/note-color/issues/10) — "Octave-aware multi-pitch note detection mechanism"

Question: what's the best concrete mechanism for note-color to detect up to 6
simultaneous notes *with octave* (a full MIDI note number, not just a
pitch-class 0–11) per analysis hop, ideally with a per-note confidence/energy
value — fitting the "pure NumPy, no heavy DSP deps" constraint validated for
chroma in #2, reusing/cheaply extending the per-hop compute already validated
at ~1.75ms/hop in #8, and leaving monophonic mode untouched? #9's note-stack
stability/layout design and #6's `tab`-view resolution both assumed such a
mechanism exists; it was never designed. The chroma pipeline (#2/#3/#4) is
deliberately octave-blind by construction (12-bin, octave-folded, for
inversion-invariant chord matching), and the existing monophonic pipeline
(`pitch_detect.py` + `note_smoother.py`) outputs exactly one `(freq,
confidence)` per hop — neither produces what #9 needs.

## Recommendation for note-color

**Spectral peak-picking on the FFT magnitude spectrum already computed for
chroma (#2), with quadratic interpolation per peak to refine each peak to a
full MIDI note number, plus a lightweight harmonic-consistency pruning pass
so a single note's own overtones aren't mistaken for separate notes. Not
iterative YIN/spectral-subtraction (Klapuri-style multi-F0) — too much added
complexity and, per Klapuri's own published error analysis, most error-prone
on exactly the consonant intervals chords are built from. Not
octave-by-convention — it produces no measured confidence value at all,
which #9's design explicitly needs to gate attack/release hysteresis, and it
silently misplaces common real voicings.**

Concretely:

1. **Spectrum: reuse, don't recompute.** Chord mode already computes
   `|rfft(x, 4096)|`, the 2049-bin magnitude spectrum, once per hop for
   chroma folding (#2) — `chroma_fb @ magnitude_spectrum` and the bass-chroma
   fold (#4) both consume it today. Peak-picking runs on that same array; it
   adds zero new FFT cost, identical to how chroma itself added none on top
   of YIN's FFT.

2. **Peak detection.** Restrict the search to bins covering
   `config.FMIN..config.FMAX` (65–1000Hz, the app's existing C2–B5 range —
   same bins YIN already searches, so no new range decision is introduced).
   At 4096 points / `SAMPLE_RATE=22050`, bin width is `22050/4096 ≈ 5.383Hz`
   (as computed in #8 for the bass-chroma cutoff), i.e. bins ~12–186. Find
   local maxima via a vectorized `(mag[1:-1] > mag[:-2]) & (mag[1:-1] >
   mag[2:])` comparison, keep those above a magnitude floor relative to the
   frame's peak magnitude (new constant `CHORD_PEAK_MIN_MAG_RATIO`, e.g.
   0.1× the frame's strongest bin — analogous in spirit to YIN's
   `YIN_THRESHOLD` and the chord-matching's 0.80 cosine threshold from #4,
   provisional pending tuning), and keep at most a generous candidate cap
   (e.g. 20) by magnitude before the next two steps — real magnitude
   spectra rarely produce more than a few dozen candidate maxima in the
   65–1000Hz band even for a dense chord.

3. **Sub-bin refinement via quadratic interpolation.** For each candidate
   peak at bin `k`, fit a parabola through `(k-1, k, k+1)`'s log-magnitude
   values and take its vertex as the refined bin position — the standard
   technique for sinusoidal peak refinement in a zero-padded FFT (Smith,
   J.O., "Quadratic Interpolation of Spectral Peaks," in *Spectral Audio
   Signal Processing*, online book, CCRMA, Stanford,
   [ccrma.stanford.edu/~jos/sasp/Quadratic_Interpolation_Spectral_Peaks.html](https://ccrma.stanford.edu/~jos/sasp/Quadratic_Interpolation_Spectral_Peaks.html)).
   This is the same three-point-parabola idea `pitch_detect.py` already uses
   on the CMNDF at line 67–71 (`x0, x1, x2 = cmnd[tau-1], cmnd[tau],
   cmnd[tau+1]`), just applied to spectral bins instead of lag values — no
   new numerical technique for the codebase, only a new place it's applied.
   Convert the refined bin's frequency to a MIDI note the same way
   `note_smoother.py` already does (`midi = 69 + 12*log2(freq/440.0)`,
   rounded), giving `(pitch_class, octave) = (round(midi) % 12, round(midi)
   // 12 - 1)` per peak — a full octave-aware note, not just a pitch class.

4. **Harmonic-consistency pruning.** Sort surviving candidates by magnitude,
   descending. Walk the list, accepting a candidate as an independent note
   unless its refined frequency falls within a tolerance (new constant
   `CHORD_HARMONIC_TOLERANCE_CENTS`, e.g. 35 cents — under a third of a
   semitone, loose enough to survive interpolation error, tight enough not
   to eat a real adjacent semitone) of an integer multiple (2×–5×, checked
   in turn) of an *already-accepted* peak's frequency. This is the step
   that keeps a single note's own 2nd/3rd/4th harmonics — which chroma's
   own harmonic-summation step (#2) deliberately reads as reinforcing
   evidence for one pitch class — from being miscounted as up to 5 separate
   "notes" here. It's a direct application of harmonic product/sum spectrum
   logic (see Survey) evaluated only at the already-picked candidate
   frequencies, not as a full dense per-bin sweep. Stop once 6 notes are
   accepted or candidates are exhausted — this is where the "up to 6" cap
   from the chord template dictionary (#3) and #9's note-stack design is
   enforced.

5. **Per-note confidence.** Each accepted peak's confidence is its
   magnitude normalized by the frame's total in-band spectral energy (or
   simply by the strongest peak's magnitude — either is a cheap, already-
   available number, unlike option 3 which produces none). This is exactly
   the "per-note confidence/energy value" #9's attack/release hysteresis
   (`NOTE_STACK_ATTACK_HOPS`/`NOTE_STACK_RELEASE_HOPS`) was designed
   assuming would exist.

6. **New config constants** (all provisional pending empirical tuning, same
   spirit as #4's 0.80 cosine threshold and #5/#9's hop counts):
   `CHORD_MAX_NOTES = 6`, `CHORD_PEAK_MIN_MAG_RATIO`,
   `CHORD_HARMONIC_TOLERANCE_CENTS`, `CHORD_MAX_PEAK_CANDIDATES` (the
   pre-pruning cap, e.g. 20).

7. **Relationship to the existing chroma/matching pipeline: additive, not a
   replacement.** The chord *name* still comes entirely from chroma +
   template matching (#2/#3/#4), unchanged. This mechanism runs off the
   same shared magnitude spectrum in parallel and only feeds the *note
   stack* (which colors/octaves to render) that #9's stability/layout
   design consumes — the two consumers (chord-name text and note-stack
   layout) stay architecturally separate, as #9 already assumed.

## Survey of established techniques considered

### Spectral peak-picking with quadratic interpolation (adopted)

Described in the recommendation above. Core technique (quadratic/parabolic
interpolation of a magnitude-spectrum peak) is standard and freely
documented: Smith, J.O., *Spectral Audio Signal Processing*, CCRMA, Stanford,
["Quadratic Interpolation of Spectral Peaks"](https://ccrma.stanford.edu/~jos/sasp/Quadratic_Interpolation_Spectral_Peaks.html)
— fits a parabola through the peak bin and its two neighbors in
log-magnitude and takes the vertex as the refined frequency estimate, exact
for a Gaussian-windowed peak and a good approximation for the Hann/Hamming
windows typically used in practice. note-color's own `pitch_detect.py`
already performs the identical three-point parabolic-fit technique on the
CMNDF (lines 67–71) to refine YIN's integer-lag estimate — this recommendation
reuses that exact idea, not a new numerical method, just a new
domain (frequency bins instead of autocorrelation lag).

Chosen because it is the cheapest of the three ticket-listed candidates by a
wide margin (one vectorized scan + a handful of 3-point fits, all off a
spectrum already computed), produces a genuine per-note frequency (hence
octave) directly rather than by convention, and produces a natural
confidence value (peak magnitude) for free. Its main weakness — a note's own
harmonics look like extra "peaks" — is addressed directly by the
harmonic-consistency pruning step, itself informed by harmonic product/sum
spectrum logic (below), rather than by a heavier multi-pass estimator.

### Iterative YIN with spectral subtraction (Klapuri-style multi-F0) — considered, rejected

Cited by the ticket as "essentially Klapuri's method with YIN substituted
for his salience function." The actual paper, read directly: Klapuri, A.P.,
"Multiple Fundamental Frequency Estimation Based on Harmonicity and Spectral
Smoothness," *IEEE Trans. Speech and Audio Processing*, 11(6), 804–816, 2003
([DOI 10.1109/TSA.2003.815516](https://ieeexplore.ieee.org/document/1255467/);
freely hosted reprint:
[ee.columbia.edu/~dpwe/papers/Klap03-multif0.pdf](https://www.ee.columbia.edu/~dpwe/papers/Klap03-multif0.pdf)).

One correction to the ticket's framing worth noting: Klapuri's own
"predominant-F0" stage is *not* YIN — YIN is used in the paper only as one
of two reference baselines the full method is benchmarked against (§III-B).
Klapuri's actual per-iteration estimator is its own bandwise algorithm:
the preprocessed spectrum (93ms Hamming-windowed frame at 44.1kHz, with a
specific log-magnitude-warping noise-suppression step, eq. 2–4) is split
into 18 log-spaced ~2/3-octave subbands (Fig. 2); within each subband a
weight vector `L_b(n)` is computed by searching over a small set of
plausible inharmonicity offsets and summing weighted harmonic-partial
amplitudes (eq. 7–10, Table I); the per-band weights are combined into a
global weight `L(n)` by summing squared bandwise weights across bands that
select the same candidate F0. The winning F0 is the `n` maximizing `L(n)`.
Substituting note-color's existing YIN call for this stage (as the ticket
suggests) is a real simplification of the paper, not a re-implementation of
it — it drops the inharmonicity search and the bandwise-combination
robustness the paper's accuracy numbers depend on.

Subtraction stage (§II-C): the detected sound's harmonic partials are
estimated (frequency + amplitude via the same kind of windowed quadratic
interpolation used in this doc's recommendation, §II-C1) and its spectral
envelope is *smoothed* before subtraction — a multistage filter that groups
harmonic partials by common prime divisors within an octave-wide window and
replaces each partial's amplitude with `min(a_h, d_h)` against the smoothed
estimate (eq. 12) — found necessary because subtracting an unsmoothed
envelope performs measurably worse (§II-C1). The whole estimate→subtract
cycle repeats, with a separate statistical stopping rule (eq. 13–14) to
decide how many concurrent sounds are present, rather than a fixed count.

Reported accuracy (§Abstract, on a 2536-sample, 30-instrument test set):
error rates for **1 through 6** simultaneous sounds were **1.8%, 3.9%, 6.3%,
9.9%, 14%, 18%** respectively — i.e. the paper's own numbers, at its full
sophistication (inharmonicity-aware bandwise weighting + smoothness-gated
subtraction, not a bare YIN swap), degrade to nearly 1-in-5 wrong at 6-note
polyphony. More importantly for this project: §II-C2 identifies the
dominant error source as *coinciding harmonics between sounds in simple
musical-interval relations* (octaves, fifths, thirds — Fig. 5 shows error
mass concentrated exactly at intervals 2/1, 3/2, 4/3, 5/4) — precisely the
intervals chords are built from. A chord-detection feature hitting its worst
accuracy exactly on chords is a bad fit regardless of implementation effort.

Rejected for note-color on two independent grounds: (a) **implementation
cost** — a from-scratch NumPy port would need the bandwise
harmonic-weighting search, the octave-wide grouped-smoothing subtraction
filter, and a stopping-count heuristic, none of which exist in this
codebase today, versus peak-picking's reuse of an interpolation technique
already in `pitch_detect.py`; a simplified "YIN-only" version as the ticket
frames it still needs a hand-rolled subtraction/smoothing stage to avoid the
paper's own documented failure mode, and skipping that stage (naive
subtract-and-repeat) is exactly what §II-C1 shows performs worse. (b)
**compute cost, concretely**: each iteration needs its own FFT-based
autocorrelation pass — note-color's `_difference_function()` already
zero-pads to 4096 and calls `rfft`/`irfft`, ~245,760 FLOPs by the
real-FFT approximation #8 used — plus `_cmndf()`'s pure-Python
`for tau in range(1, len(d))` loop, which at `tau_max = sample_rate/FMIN =
22050/65 ≈ 339` is already 339 pure-Python iterations paid once per hop
today. Running that up to 6 times (once per removed note) multiplies both
costs roughly 6×: ~1.47M FLOPs of FFT work and ~2,000 Python-loop
iterations, before adding any subtraction/smoothing arithmetic at all — an
order of magnitude more than peak-picking's ~2,300 operations (see Latency
budget below), for accuracy that degrades on the harmonic intervals chords
consist of. Not adopted.

### Octave-by-convention from chord template + bass — considered, rejected

The ticket's own example: place the chord's other pitch classes at the
nearest octave above the detected bass note, using the already-matched
template's root/quality (#3/#4) and no per-note measurement at all.
Cheapest possible option — zero new compute — but concretely wrong for
common real voicings:

- **Open voicings**: a guitarist or pianist spreading a triad's root, third,
  and fifth across more than one octave (e.g. root in octave 2, fifth in
  octave 3, third in octave 4) — the convention collapses all three into
  one tight cluster just above the bass, misplacing at least two of three
  notes.
- **Drop-2 voicings** (common in jazz piano/guitar comping): the
  second-highest voice of a close-position chord is dropped an octave —
  by definition a note that is *not* at "nearest octave above bass," which
  is exactly the position the convention would assign it.
- **Upper extensions** (9th/11th/13th): idiomatically voiced an octave or
  more above the chord's core (a 9th voiced as a 9th, not a 2nd), not
  stacked immediately above the bass the way a convention-only placement
  would assume.

Beyond the voicing-accuracy problem, this option fails the ticket's stated
*requirement*, not just its aesthetic preference: it produces no measured
per-note confidence/energy value at all, since no measurement happens. #9's
attack/release hysteresis (`NOTE_STACK_ATTACK_HOPS`/`RELEASE_HOPS`) is
designed around a confidence signal to gate on — a convention has none to
offer, so #9's mechanism would have nothing to hysteresis against for
individual notes appearing/disappearing (only the chord-level match
changing, which #5 already handles separately). Rejected.

### Harmonic product spectrum / harmonic sum spectrum (prior art, informs the pruning step)

Schroeder, M.R., "Period Histogram and Product Spectrum: New Methods for
Fundamental-Frequency Measurement," *J. Acoust. Soc. Am.* 43(4), 829–834,
1968; Noll, A.M., "Pitch Determination of Human Speech by the Harmonic
Product Spectrum, the Harmonic Sum Spectrum, and a Maximum Likelihood
Estimate," *Proc. Symposium on Computer Processing in Communications*,
Polytechnic Press, 1970, pp. 779–797. (Both are the standard originating
citations for HPS/HSS across the pitch-detection literature; neither is
freely hosted at a stable URL as of this research — corroborated
consistently across secondary summaries and course material, e.g. Duan, Z.,
"Topic 4: Single Pitch Detection," University of Rochester ECE 477 lecture
notes, and UCSD Music's HPS reference page, in the same manner the #2
research doc corroborated Fujishima's paywalled original via consistent
secondary description.)

HPS/HSS: downsample the magnitude spectrum by successive integer factors
`2, 3, ..., H` (equivalent to evaluating the spectrum at `2f, 3f, ..., Hf`
for every candidate `f`), then multiply (HPS) or sum (HSS) the aligned
copies together; a true fundamental's harmonics reinforce at every factor,
producing a sharp combined peak at `f0` even when `f0`'s own bin is weak or
absent, while a spurious candidate's downsampled copies mostly don't align
with real energy. Cost is `O(H·N)` for a dense per-bin evaluation across the
whole spectrum.

Not adopted as the top-level multi-pitch mechanism itself: running full HPS
repeatedly to extract up to 6 F0s requires the same
iterative-estimate-and-remove structure as Klapuri's method (find the
strongest HPS peak, remove it, repeat), inheriting the same harmonic-collision
problem discussed above with less-validated accuracy behavior than Klapuri's
purpose-built bandwise/smoothness treatment — worse of both worlds for this
project. Instead, its core logic — "does energy exist at integer multiples
of this candidate frequency?" — is reused narrowly, evaluated only at the
handful of already-picked peak-picking candidates (step 4 of the
recommendation), not as a dense full-spectrum sweep. This keeps the benefit
(harmonics don't get miscounted as separate notes) without the cost or
complexity of running HPS as the primary detector.

### Cepstral multi-pitch methods — not pursued

Cepstral pitch detection (liftering a periodicity peak out of the
log-magnitude spectrum's own spectrum) is a well-established single-F0
technique, but doesn't extend cleanly to multi-pitch: multiple
simultaneously-sounding F0s interact multiplicatively in the log-magnitude
domain before the second transform, producing cross-terms in the quefrency
domain that don't correspond cleanly to individual notes' periodicities the
way peaks in a linear magnitude spectrum correspond to individual
partials. Per the ticket's own instruction not to force in prior art that
isn't genuinely relevant, this isn't pursued further — the linear-spectrum
peak-picking approach above is a better match to this project's actual
signal model (a small number of near-harmonic tone complexes summed
linearly, not one complex broadband source).

## Latency / compute budget

Reusing #8's method: FLOP-count the new arithmetic, add a generous
NumPy-call-overhead ceiling, and check both the per-hop (~23.2ms) and
end-to-end (<150ms) budgets on the same conservative target hardware
(Raspberry Pi Zero 2 W, quad-core Cortex-A53 @ 1GHz, single-core relevant
here as before).

**1. Local-maxima scan.** In-band search covers bins ~12–186 (65–1000Hz at
4096-pt/22050Hz, `22050/4096 ≈ 5.383Hz/bin`, per #8's own bin-width
calculation) — 174 bins. A vectorized `(mag[1:-1] > mag[:-2]) & (mag[1:-1] >
mag[2:])` comparison is ~2 comparisons/bin: `174 × 2 ≈ 350 operations`.

**2. Threshold + top-candidate selection.** Boolean-mask the ~350-operation
result against `CHORD_PEAK_MIN_MAG_RATIO`, then `np.argpartition` down to
`CHORD_MAX_PEAK_CANDIDATES` (20) candidates from a realistic worst case of a
few dozen local maxima: `O(M)` for `argpartition`, call it `~50 operations`
for a generously-sized `M≈50`.

**3. Quadratic interpolation per candidate.** Each of up to 20 surviving
candidates gets a 3-point parabolic fit — 2 subtracts, 1 multiply, 1 divide
per fit (identical arithmetic to `pitch_detect.py`'s existing CMNDF
parabola, lines 67–71): `20 × 4 ≈ 80 FLOPs`. Frequency→MIDI conversion adds
one `log2` call per candidate — folded into call overhead below rather than
counted as a FLOP.

**4. Harmonic-consistency pruning.** Worst case: 20 sorted candidates, each
checked against up to 6 already-accepted peaks across up to 5 harmonic
multiples (2×–5×+1, generously counted as 5 checks): `20 × 6 × 5 = 600`
checks, each a subtract + `abs` + compare against a tolerance (~3 operations):
`600 × 3 = 1,800 operations`. Unlike steps 1–3, this loop is not naturally
vectorizable (each accept/reject decision depends on prior decisions in the
same pass), so it runs as a plain Python loop, not a single NumPy call —
costed separately below rather than folded into the FLOP total.

**Total new FLOP-equivalent arithmetic: `350 + 50 + 80 ≈ 480` operations**
(steps 1–3; step 4's 1,800 operations are pure-Python-loop-bound, see
below). Using #8's same bottom-up conversion — 8 GFLOPS theoretical
single-core peak on the Zero 2 W's Cortex-A53, derated to a pessimistic 1%
efficiency (80 MFLOPS effective) for small, non-compute-bound operations:
`480 / 80,000,000 ≈ 0.006ms` — negligible, as expected for an operation
count two orders of magnitude below the chroma fold's ~60k FLOPs.

**Python/NumPy call overhead**, using #8's same 100µs/call ceiling
(deliberately over-padded, per #8's own framing): steps 1–3 involve on the
order of ~6 NumPy calls (two comparison arrays, a boolean mask, an
`argpartition`, a batched subtract/multiply/divide for interpolation, a
`log2` call): `6 × 100µs = 0.6ms`.

**Step 4's Python loop**, costed separately since it's not a NumPy call:
up to `20 × 6 = 120` inner-loop passes (worst case, before an accept stops
early) of a few scalar arithmetic operations each. CPython bytecode
dispatch on a 1GHz in-order Cortex-A53 (no JIT, no out-of-order execution to
hide dispatch latency) is pessimistically costed here at up to 5µs per pass
— several times any per-iteration cost actually reported for simple
CPython loops even on much slower embedded targets, kept deliberately high
in the same spirit as #8's over-padded call-overhead ceiling:
`120 × 5µs = 0.6ms`.

**Combined pessimistic new cost per hop: `0.006 + 0.6 + 0.6 ≈ 1.2ms`**,
rounded up to **~1.3ms** for headroom.

### Total against the budget

This is *additive* to #8's already-validated ~1.75ms/hop (chroma fold + bass
chroma fold + 360-template matching) — the new mechanism doesn't replace or
shrink that work, both run every hop chord mode is active.

| Component | Per-hop cost |
|---|---|
| Chroma fold + bass fold + template matching (#8, already validated) | ~1.75ms |
| Octave-aware peak-picking (this doc) | ~1.3ms |
| **Total chord-mode compute** | **~3.05ms** |

| Budget | Value | Pessimistic total | Margin |
|---|---|---|---|
| Per-hop (must keep up with the audio stream) | ~23.2ms | ~3.05ms | ~7.6× |
| End-to-end target | <150ms | ~3.05ms (added to an otherwise-unchanged pipeline) | ~49× |

Margin shrinks from #8's standalone ~13×/~85× to ~7.6×/~49× once this
mechanism's cost is stacked on top, but both remain comfortable — the
per-hop margin in particular stays well above 1×, so the analysis thread
still finishes each hop's work before the next block arrives and the
drop-oldest queue (`QUEUE_SIZE=8`) is never pressured. As in #8, a Pi 4
(Cortex-A72, out-of-order, ~1.8x the clock) gives meaningfully more margin
than this Zero 2 W floor.

## Open caveats

- **Harmonic-vs-independent-note ambiguity is the central risk, and it's a
  heuristic, not a proof.** Step 4's tolerance-based pruning
  (`CHORD_HARMONIC_TOLERANCE_CENTS`) is a rule this doc designed for this
  project, not a technique lifted from validated literature the way the
  interpolation step is — it needs empirical tuning against real chord
  recordings once implemented, in the same "provisional pending empirical
  tuning" spirit as #4's 0.80 cosine threshold and #5/#9's hop-count
  constants. Too tight and real closely-voiced notes get merged; too loose
  and a note's own harmonics leak through as phantom extra notes.

- **Does this inherit CLAUDE.md's known ~100ms octave-error blip?** No, not
  directly — that limitation is specific to YIN's time-domain
  autocorrelation locking onto a sub-harmonic period as a note's amplitude
  fades (an ambiguity in the ACF, not the spectrum), and monophonic mode's
  YIN path is untouched by this ticket. This mechanism has its own,
  different decay-time failure mode instead: as a note's peak magnitude
  fades toward the noise floor, it can intermittently drop below
  `CHORD_PEAK_MIN_MAG_RATIO` and flicker in and out of the accepted-6 list
  near the end of its decay. This is exactly the flicker #9's attack/release
  hysteresis (`NOTE_STACK_RELEASE_HOPS=4`, biased toward staying on) was
  designed to absorb — so it's a known, already-covered problem, not a new
  gap this ticket introduces.

- **Low-register frequency resolution.** The #2 research doc already
  established that the 2048-sample/93ms window's ~10.77Hz native bin
  spacing (before zero-pad interpolation, which sharpens but doesn't add
  resolution) exceeds semitone spacing near C2 (~3.9Hz at 65Hz). Chroma
  compensates via harmonic summation across pitch classes; peak-picking has
  no equivalent escape hatch, because it needs the *actual* fundamental
  peak's position to place an octave, not just a semitone-class bucket —
  two real chord tones a semitone or closer apart in the low register can
  genuinely fail to resolve into two distinct spectral peaks and get
  reported as one note. Expected to matter only for tightly-voiced low
  clusters (e.g. two notes a major second apart below ~C3); not fixed here,
  left as a real, open limitation for empirical tuning/possible future
  mitigation (e.g. cross-checking an ambiguous fundamental region against
  its harmonics) rather than blocking this ticket.

- **Confidence-value calibration is unvalidated.** Peak magnitude (or
  magnitude normalized against in-band energy) is a reasonable, cheap proxy
  for per-note confidence, but its actual numeric range/behavior against
  #9's `NOTE_STACK_ATTACK_HOPS=2`/`RELEASE_HOPS=4` hysteresis hasn't been
  exercised on real audio — needs joint tuning with #9's implementation,
  not assumed correct from this design alone.

- **What this approach gets right relative to option 3, concretely**: two
  notes an octave apart (root doubled up, a very common voicing) are two
  genuinely separate spectral peaks at different frequencies and are picked
  up as two independent notes here, each with its own confidence — a case
  the octave-by-convention approach structurally cannot represent (it has
  only one instance of each pitch class to place). Noted here as a concrete
  point in favor, not just a caveat.
