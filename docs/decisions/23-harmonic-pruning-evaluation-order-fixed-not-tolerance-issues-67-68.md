# Harmonic-pruning evaluation order fixed, not tolerance (issues #67/#68)

Issue #67's own hypothesis — that a fixed `harmonic_tolerance_cents`
doesn't scale correctly with harmonic number, so real-world frequency
jitter on the fundamental defeats pruning at higher harmonics — was
checked directly and largely ruled out: cents are already a relative/
logarithmic unit, so a fixed cents tolerance applied to
`predicted = accepted_freq * n` is mathematically invariant to `n` for a
proportional (relative) frequency error, and a synthetic sweep detuning a
note's own 3rd harmonic by up to 30 cents (fundamental still the loudest
peak) never broke the existing 35-cent tolerance — well past any
plausible real jitter at this app's bin resolution (~5.4Hz at
`config.WINDOW_SIZE`/2x-zero-padded FFT).

Instrumenting `multipitch.detect()`'s raw candidate list directly (per
both issues' own guidance) found the actual bug instead: the pruning loop
walked candidates in magnitude-descending order, and `_is_harmonic_of()`
only prunes a candidate that's a harmonic *of* an already-accepted one —
it has no reverse check for "is this already-accepted candidate itself a
harmonic of a not-yet-accepted, lower note." Reproduced deterministically
(no jitter needed) by synthesizing a single E4 whose 3rd harmonic partial
carries more amplitude than its own fundamental (plausible under real
mic/speaker frequency response or room-reflection comb filtering, both of
which can null a fundamental's bin or boost an overtone's): the louder
harmonic got accepted first (nothing to compare against yet), and the
true fundamental, evaluated afterward, isn't a harmonic of anything
*higher* in frequency, so it got accepted too — reported as two notes
(E4 real + B5 phantom) instead of one. This exactly reproduces issue #67's
measured symptom, including the "single isolated note shows a phantom
2nd/3rd-harmonic note" observation that ruled out cross-note interaction.

Fix: walk candidates ascending by frequency instead of descending by
magnitude for the pruning pass (capping to `max_notes` by magnitude
afterward, once pruning is done, to preserve the existing "keep the
loudest surviving notes" behavior). This guarantees a real fundamental
always gets first claim on an accepted slot, so its own harmonics
reliably prune against it regardless of which partial the FFT happened to
weight louder that hop — verified against the loud-3rd-harmonic
repro above, and against a 30-cent-detuned-harmonic variant, with zero
regressions across the existing `test_multipitch.py`/`test_chord_
templates.py`/`test_chord_smoother.py` suites (including issue #56's
original over-calling fix).

**Issue #68's residual gap after the ordering fix.** Re-running the
ordering-fixed `detect()` against `scripts/acoustic_pipeline_test.py`'s
own `DENSITY_VOICINGS` synthetic fixtures (no live audio, just the same
note lists run through additive synthesis) showed several of those
specific voicings still drop a real note — always the same pattern: one
note's frequency sits within a couple of cents of another note's own
harmonic (e.g. A2 and E4 — a root and the fifth an octave above it, a 3:1
ratio; 12-TET's fifth+octave is only ~2 cents from the true 3rd harmonic).
This is a *different* bug from #67's: it's not order- or
magnitude-dependent, and it isn't fixed by the ordering change, because
it isn't really a bug in the pruning logic at all — it's an inherent
ambiguity. A single hop's magnitude spectrum cannot distinguish "this
peak is note X's own 3rd harmonic" from "this peak is a real,
independently-sounding note that happens to land within a couple of
cents of note X's 3rd harmonic": the two scenarios are spectrally
identical events (one peak at 3x another's frequency).

Two more targeted fixes were tried and rejected before settling on
"document as a known limitation":

- *Narrowing `harmonic_tolerance_cents`* (tried down to 5 cents): fixes
  none of the near-exact collisions (the coincidence itself is ~2 cents,
  well inside any tolerance still wide enough to catch real acoustic
  jitter) and introduces new phantom notes elsewhere (a legitimate
  overtone's own jitter starts escaping a too-narrow tolerance) — a wash,
  not a fix, exactly the "don't blindly loosen/tighten the threshold"
  risk both issues warned against.
- *A magnitude-consistency requirement* (only prune a harmonic candidate
  if its magnitude doesn't exceed what a decaying overtone series would
  predict from the accepted fundamental's own magnitude): directly
  reopens issue #67. #67's real acoustic failure mode *is* a genuine
  overtone measuring louder than its own fundamental (that's the whole
  bug) — a magnitude-consistency check would refuse to prune exactly the
  case #67 needs pruned, at exactly the rate it would fix #68's
  collision cases. Confirmed by re-running the loud-3rd-harmonic repro
  with this rule added: the phantom note returns.
- A candidate's own corroborating harmonic series (does 2x/3x of *this*
  peak also show up as a separate peak, as evidence it's a real,
  independent fundamental rather than just another note's overtone) was
  also considered and rejected: a single real note's own natural overtone
  series produces exactly the same self-corroborating pattern, so this
  discriminator can't tell "real independent note" from "the accepted
  note's own next overtone" either — confirmed by checking the accepted
  fundamental's own lower harmonics in the same failing test cases.

`chord_smoother._update_note_stack()`'s max_notes trimming — the issue's
second named hypothesis — was checked directly and is *not* buggy:
seeded with 6 constant, always-detected synthetic candidates it retains
all 6 indefinitely, and seeded with a worst-case transient (a full
6-note chord change, 12 candidates briefly competing for 6 slots) it
fully converges onto the new chord's correct 6 notes within a handful of
hops (see `tests/test_chord_smoother.py`'s
`test_real_pipeline_retains_all_six_notes_of_a_dense_non_colliding_chord`/
`test_note_stack_trimming_converges_to_new_chord_after_a_few_hops`). The
residual #68 gap lives entirely in `multipitch.detect()`'s pruning layer,
not the smoother.

Given no scoped fix resolves the collision case without reopening #67,
and resolving it properly would need information beyond a single hop's
magnitude spectrum (e.g. per-pitch-class onset/persistence tracked across
hops — a materially bigger change than tuning existing pruning logic),
this is recorded as a known, inherent limitation (see CLAUDE.md's Known
limitations) rather than force-fit with a change that trades one bug for
another. Chord voicings without such coincidental intervals are fully
fixed by the ordering change alone — confirmed with a dense 6-note chord
built with a >=60-cent safety margin from any small-integer frequency
ratio (`tests/test_multipitch.py`'s
`test_dense_six_note_chord_all_survive_when_not_harmonically_colliding`).
