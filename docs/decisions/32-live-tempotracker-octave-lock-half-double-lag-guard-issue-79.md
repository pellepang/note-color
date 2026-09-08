# Live `TempoTracker` octave-lock (half/double-lag) guard (issue #79)

**The problem.** `TempoTracker._estimate()` picks its tempo candidate via
a single `np.argmax()` over the autocorrelation window — no ascending
threshold scan the way YIN uses, just "the single tallest lag in the
valid BPM range." That's structurally biased toward finding whichever
periodicity is loudest/strongest in the novelty history, which is often
a strong subdivision (e.g. eighth notes) rather than the actual,
musically slower beat — exactly the "octave error" failure mode
`docs/research/oss-landscape-rhythm-tempo.md` documents as the single
most common failure across real causal beat-trackers (madmom's online
mode, BeatNet), consistently costing several accuracy points versus
their offline counterparts.

**Why a naive "check acf[2\*best_lag]/acf[best_lag//2]" doesn't work.**
The issue's own suggested approach — compare the autocorrelation array at
half/double the winning lag against the same confidence gate — sounds
free (the array's already in memory) but two things make it genuinely
hard to calibrate safely, confirmed empirically before writing any
production code:

1. **A plain, non-alternating periodic signal's acf at `2*best_lag` is
   *already* close to its value at `best_lag`, and that closeness grows
   with tempo/window length alone — nothing to do with genuine
   alternation.** Measured directly: a clean, single-period impulse
   train at production-realistic settings (`TEMPO_HISTORY_SECONDS=8.0`,
   `hop_seconds≈0.0232`) shows `acf[2L]/acf[L]` ratios of 0.86 (60bpm) up
   to 0.96 (200bpm) — a fixed-ratio "prefer the double-lag if it's within
   X% of the winning peak" test would have to be so loose it either
   never fires or constantly, wrongly, halves fast tempo estimates. This
   ratio's growth with lag count is a real, provable mathematical
   property of delta-train autocorrelation (`acf[k*L]/acf[0] == (periods
   - k) / periods` exactly, confirmed against every plain-train test
   case to 4+ decimal places), not noise — a naive ratio check confounds
   "genuine alternating structure" with "how many periods fit in the
   window," the exact tempo-dependent confound a fixed threshold can't
   see.
2. **Correcting for #1 with a linear-decay baseline (`expected_2L =
   2*acf[best_lag] - acf[0]`, then measuring how far `acf[2*best_lag]`
   exceeds that prediction as "excess") is exact for a noiseless
   signal — but introduces its own bias under realistic additive
   noise.** Superposing independent noise onto a plain periodic train
   inflates `acf[0]` (its own zero-lag self-energy) without
   correspondingly inflating `acf` at any nonzero lag — algebraically,
   this shows up as `excess ≈ noise_energy / acf_total[0]`, a purely
   noise-driven, always-positive bias completely unrelated to any real
   alternating structure. Measured: a plain train with unit-amplitude
   impulses and additive Gaussian noise (sigma 0.15-0.25) produced
   `excess` values of 0.14-0.44 — squarely overlapping with genuinely
   alternating signals' excess (0.03-0.67 depending on how pronounced
   the alternation was). A naive excess-only threshold would false-fire
   on ordinary noisy real audio constantly.

**The fix actually shipped.** `TempoTracker._resolve_octave_lock()`
subtracts `(1 - confidence)` from the linear-decay `excess` measure
before thresholding it (`config.TEMPO_OCTAVE_LOCK_MARGIN`, calibrated to
0.08). This empirically cancels the noise-driven bias from point 2 above
— since the same noise energy that inflates `excess` also depresses
`confidence` (`acf[best_lag]/acf[0]`) by a comparable amount, subtracting
`(1 - confidence)` removes most of the shared noise term while leaving
genuine alternating structure's contribution mostly intact. Measured
across tempos 90-200bpm and noise sigma 0-0.25 (against unit-amplitude
impulses): a plain, non-alternating signal's *adjusted* excess stayed
`<= ~0.05` in every tested combination; a genuinely alternating signal
(>=30% amplitude difference between the true beat and its in-between
subdivision) measured `>= ~0.03-0.19` depending on how pronounced the
alternation was. `TEMPO_OCTAVE_LOCK_MARGIN = 0.08` sits with real margin
above the plain-signal ceiling while still catching clearly alternating
structure.

**Deliberately one-directional.** The guard only ever corrects *towards*
the slower (2x) reading, never the faster (0.5x) one — `argmax` already
returns the single tallest candidate across the *entire* valid-BPM
window, so a same-window faster candidate can, by construction, never be
taller than what `argmax` already picked; there's no symmetric
"half-lag" case to exploit the way pitch's YIN sub-harmonic check exploits
an *ascending* threshold scan's bias toward the shortest crossing tau.
Checking `acf[best_lag // 2]` (as the issue's own text also floated)
would only ever look at a candidate that was already available to
`argmax` and lost — no new information there.

**Deliberately conservative on mild/ambiguous alternation.** A subtle
"ghost" subdivision only ~10% quieter than the true beat (a plausible
stand-in for ordinary dynamics variation on what's actually a single,
uniform-tempo pulse) measured adjusted excess around -0.06 — safely
*below* the 0.08 threshold, so it's left uncorrected. This is a
deliberate choice, not a missed case: the alternative (correcting on any
detectable amplitude variation) would risk constantly halving legitimate
fast, evenly-played tempos whose only "alternation" is normal
performance dynamics.

**Validated.** `tests/test_tempo_tracker.py`'s three new tests
(synthetic impulse trains, same "synthesize the signal, no binary
fixtures" convention as `test_chroma.py`'s `make_tone()`):
`test_octave_lock_correction_prefers_true_beat_over_strong_subdivision`
(a 100bpm true beat with a much quieter 200bpm subdivision — corrects
100->recovered, was 200 uncorrected), `test_mild_amplitude_variation_
does_not_trigger_false_correction` (ghost_amp=0.9 — stays at the naive
200bpm reading, confirming the conservative scoping above),
`test_plain_periodic_trains_are_unaffected_by_the_octave_lock_guard`
(three different plain periods — bit-identical estimates to before this
change). All existing `test_tempo_tracker.py` tests (issue #70's
confidence-gate tests included) pass unchanged. Full suite: 405 passed.

**Known limitation, stated plainly.** Only validated against synthetic
signals so far — `scripts/rhythm_accuracy_test.py`'s real `--source
loopback` round trip (the issue's own suggested validation path) was not
run for this change, since it requires a real audio loopback session
this environment doesn't have. Same provisional posture already
established for issues #69 and #71: treat `TEMPO_OCTAVE_LOCK_MARGIN =
0.08` as synthetic-calibrated, not field-confirmed, until a real-mic/
loopback re-verification happens. If real playing shows it too loose
(false corrections on legitimate fast, steady tempos) or too tight
(missing real subdivision-lock errors), the fix is a one-constant
recalibration, same low blast radius as issue #71's own margin fix.
