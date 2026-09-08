# Monophonic YIN octave-doubling in the low register fixed with a sub-harmonic sanity check (issue #69)

Issue #69's acoustic round-trip test found the monophonic YIN tracker
(`pitch_detect.detect_pitch()`) frequently locking onto a note's own 2nd
or 4th harmonic instead of the true fundamental, specifically in octave 2
(~65-123Hz) — note-specific (A2/C2/F#2 badly wrong or silence-gated;
D#2/E2/F2/A#2/B2 rock solid), not a uniform "bass is hard" story.

**Root cause, confirmed empirically (not the window-size hypothesis the
issue's own repro guidance suggested).** `detect_pitch()`'s tau-selection
scans lags ascending from `tau_min` and locks onto the *first* one whose
CMND (cumulative mean normalized difference function) dips below
`YIN_THRESHOLD`. Reproduced with synthesized additive tones (harmonics
1-4, weighted like `chroma.HARMONIC_WEIGHTS`) fed straight to
`detect_pitch()`: when a note's fundamental is weak relative to its own
harmonics (empirically representative of real playing — bass rolloff in
small speakers/mics, or just a harmonic-rich low tone whose energy skews
upper-partial), a strong harmonic produces its *own* confident
sub-threshold CMND dip at an exact submultiple of the true fundamental's
period — and because that shorter lag is scanned first, it wins,
regardless of how much deeper the true (longer) fundamental's own dip
would have been. `config.WINDOW_SIZE` turned out fine (octave 2 still
gets 6-10 full periods per window) — the bug is purely in which
sub-threshold dip the scan accepts, not a resolution problem.

Confirmed via direct inspection of the CMND curve (e.g. a weak-fundamental
A2 tone): the true fundamental's dip (tau≈200 samples) was ~10x deeper
(more confident) than the harmonic-submultiple dip the scan locked onto
first (tau≈50, a 4th-harmonic-driven false lock reading as A4). This
directly matches the issue's own "search further for a deeper/more
confident local minimum" and "check smaller integer-multiple lags, prefer
the strongest" suggestions — but a naive version of either (just compare
raw CMND depth among `tau`, `2*tau`, `3*tau`, `4*tau`) turned out to
*regress* plenty of already-correct octave 3-5 detections: integer-sample
rounding of the true (non-integer-sample) period means a coincidental
multiple can land closer to an exact grid sample than the true tau does,
making its raw CMND value spuriously lower with no real periodicity
advantage — verified directly (e.g. C3/F5/G#5 test tones flipped to a
wrong lower octave under the naive version, including *pure sine* tones
with no harmonic ambiguity at all, which should never regress).

**The fix**, entirely inside `detect_pitch()` (`pitch_detect.py`):
1. After the ascending scan finds a sub-threshold candidate `tau`, check
   small integer multiples (`2*tau` through `4*tau`, matching
   `chroma.HARMONIC_WEIGHTS`' own harmonics-1-4 convention —
   `config.YIN_SUBHARMONIC_MAX_MULTIPLE`) for a *parabolically-refined*
   CMND value (not the raw grid value — the refined, sub-sample-accurate
   vertex value from the same 3-point parabola `detect_pitch()` already
   uses for frequency refinement) that both clears the threshold and beats
   the candidate's own refined value by a real margin
   (`config.YIN_SUBHARMONIC_MARGIN`, 0.5 — i.e. at least half as deep).
   Using the *refined* value rather than the raw grid sample is what
   defeats the integer-rounding false-positive above: it estimates the
   true continuous-domain minimum regardless of which exact grid point
   happened to be the nearest integer lag.
2. Skip the check entirely when the original candidate is *already* very
   confident (refined CMND below `config.YIN_SUBHARMONIC_SKIP_CMND`,
   0.01) — an already-correct detection is *also* trivially periodic at
   its own integer multiples (any period-T signal repeats at 2T, 3T, ...
   by definition), so even the refined-value comparison could otherwise
   occasionally flip a genuinely-correct, high-confidence detection. This
   gate is what keeps octave 3-5 (and plain single-sine tones, which have
   no harmonic content to be ambiguous about at all) untouched.

Verified: all 6 of the issue's reported failing octave-2 frequencies now
land within a semitone of the true fundamental across a range of
harmonic-weight profiles (flat, bass-rolloff-weighted, near-fundamental-
silent), a broad sweep across all 12 pitch classes × octaves 2-5 × several
harmonic profiles shows zero regressions, and the fix is robust to
additive noise up to a 0.2-amplitude floor against the reported
frequencies' harmonic tones. A handful of maximally-degenerate synthetic
weight combinations (e.g. two of four harmonics reduced to near-zero,
leaving almost all energy on a single upper harmonic) remain uncorrected
— a genuine physical ambiguity when the fundamental is essentially
inaudible, not a realistic harmonic profile, and out of this fix's scope.
`tests/test_pitch_detect.py::test_octave2_harmonic_rich_tone_not_octave_doubled`
is the regression test for the 6 originally-reported frequencies.

### Follow-up: the fix itself regressed real-mic accuracy, root-caused and recalibrated (issue #69, round 2)

A second real speaker→mic re-verification round found the fix above made
things *worse*, not better: octave-2 recall dropped from 11/12 to 8/12
notes ever detected (four notes, including three that were previously
100% accurate, went completely undetected), overall chromatic recall fell
from 97.9% to 91.7%, and several notes that were 100% accurate *before*
this fix — D#2, E2, G2 — became unstable (15-34% steady-state accuracy)
afterward, alongside a new regression on octave-3's C3. This wasn't "not
fully fixed" — it was the fix actively breaking previously-correct
detections, so the issue was reopened rather than left closed.

**First finding: the existing regression test doesn't actually exercise
the fix.** `test_octave2_harmonic_rich_tone_not_octave_doubled`'s profile
(`harmonics=(1.0, 0.5, 1/3, 0.25)`, fundamental dominant, matching
`chroma.HARMONIC_WEIGHTS`) turns out to already detect correctly with the
subharmonic check fully disabled (`subharmonic_max_multiple=0`) — the
scan's own ascending-threshold-then-walk-to-local-minimum behavior was
already enough for this profile, so the test was validating "detection
still works," not "the correction still fires." A genuinely adversarial
profile was needed to reproduce the *original* bug reliably at all:
`harmonics=(0.0, 0.1, 1.0, 0.2)` (fundamental fully silent, 3rd harmonic
dominant) reliably octave-doubles (or worse — lands on the 3rd harmonic,
~1902 cents off) across all 12 octave-2 pitch classes with the check
disabled, and is what
`test_octave2_silent_fundamental_dominant_3rd_harmonic_not_octave_doubled`
now formalizes.

**Root cause of the regression, found by reproducing it synthetically.**
Sweeping a dominant-fundamental octave-3 tone (the same
`chroma.HARMONIC_WEIGHTS`-shaped profile, fundamental strongest) plus
additive white noise (0.05 amplitude) and a 60Hz+120Hz "mains hum"
component (standing in for the mic self-noise/room rumble/electrical hum
a real recording — but not a clean synthesized test tone — actually
contains) reproduced the regression directly: a C3 tone's true tau (≈169
samples at `SAMPLE_RATE=22050`) was found correctly by the ascending scan,
but at only a middling confidence (refined CMND ≈0.115, close to
`YIN_THRESHOLD=0.12` — a real, noisy signal, not the near-zero CMND a
clean synthetic tone produces). The subharmonic check then examined 2×
that tau (≈337 samples, landing just inside `tau_max≈339` — the boundary
implied by `FMIN=65Hz` for `SAMPLE_RATE=22050`/`WINDOW_SIZE=2048`) and
found a *deeper* dip there (refined CMND ≈0.017) — not because of any
real periodicity at that lag, but because broadband low-frequency content
sitting near the fmin edge produces its own coincidentally-deep dip
there, independent of what note is actually playing. `YIN_SUBHARMONIC_
MARGIN=0.5` (a switch needs the candidate to be only ~2x deeper) accepted
that dip immediately, misreading a genuinely-correct C3 as C2 — an octave
error inflicted *by the correction itself*, not the original bug. This
mechanism is structural, not a one-off coincidence: it only affects
octaves whose true tau's 2x/3x/4x multiple still lands inside `tau_max`
(octave-2 notes' own tau already sits close to `tau_max`, so their own
multiples always exceed it and the subharmonic check is a no-op *for an
already-correct octave-2 candidate* regardless of margin — matching why
the real-world regression report's directly-broken notes clustered at the
octave-2/octave-3 boundary specifically). It's also, structurally, exactly
the classic YIN "subharmonic error" pitfall the original paper's
ascending-scan-plus-first-sub-threshold-dip heuristic exists to avoid in
the first place (CMND is known to trend toward spuriously low values at
larger lags, independent of real periodicity) — the original #69 fix
reintroduced a scoped version of that same pitfall by deliberately
searching larger lags for a "deeper" dip.

**The fix: recalibrate `YIN_SUBHARMONIC_MARGIN` from 0.5 to 0.1** (a
switch now needs the multiple's dip to be ~10x deeper, not ~2x), backed
by adversarial-testing data that separates the two failure modes cleanly:
sweeping fundamental weight from 0 up to the point genuine octave-doubling
stops occurring at all (across three single-extra-harmonic profiles —
2nd, 3rd, and 4th harmonic each tested alone) found genuine subharmonic-
lock ratios (best-multiple-CMND / candidate-CMND) never exceeding ~0.08;
stress-testing the mains-hum/noise regression mechanism above across all
12 octave-3 notes, hum amplitudes 0.2-1.2, both 50Hz and 60Hz mains
frequencies, and 8+ noise seeds each found the false-positive ratio never
dropped below ~0.14. A margin of 0.1 sits with real headroom inside that
gap on both sides, and — not a coincidence — lands almost exactly on this
fix's own original "~10x+ deeper in the reported failure cases" empirical
observation above, which the 0.5 value never actually reflected.
`YIN_SUBHARMONIC_SKIP_CMND` was checked and left unchanged (0.01): it
doesn't discriminate this regression at all, since the false-positive
candidate's own CMND (≈0.115 in the C3 case) sits nowhere near that
threshold — it's a genuinely correct but noise-degraded detection, not an
already-ultra-confident one the skip gate was ever meant to guard.

Re-validated after recalibration: the adversarial silent-fundamental
octave-2 sweep above still corrects all 12 pitch classes (unaffected —
margin tightening only makes the check *more* conservative, never
prevents a genuine ≥10x-deeper correction); a 20-seeds-per-note stability
sweep across all 12 octave-3 notes under the hum+noise regression
mechanism holds 239/240 trials within 50 cents (the one residual failure,
C#3 seed 1, reproduces identically with the subharmonic check fully
disabled — confirmed unrelated to this fix, an ordinary base-YIN noise
robustness limit, not something introduced or fixable here). Full
`pytest tests/` suite (302 tests, including both new adversarial
parametrized tests above plus every pre-existing #69 regression test)
green throughout.
`tests/test_pitch_detect.py::test_octave3_hum_and_noise_does_not_flip_already_correct_detection`
is the regression test for this round.

**Known residual risk, explicitly not closed out by this round.** All of
the above — both the original bug's reproduction and this round's
regression reproduction — is synthetic. This repo's `--source loopback`
acoustic pipeline test (`scripts/acoustic_pipeline_test.py`) was re-run as
a guard and stayed at 100% recall/100% steady-state accuracy on the
`chromatic` suite, same as before this round's changes — but loopback
audio has no physical mic frequency-response coloration or real room
noise at all, so (as already noted when that test infrastructure was
built) it cannot reproduce the real regression this round investigates,
and passing it is not evidence the real-mic regression is fixed. The
synthetic mains-hum/noise profile here is a plausible, reasoned proxy for
what a real mic's self-noise/room rumble/electrical hum could look like
to YIN's CMND curve — not a measurement of an actual mic. A real
speaker→mic re-verification, the same kind that caught this regression in
the first place, is the only way to confirm this recalibration holds up
in the field; issue #69 is being left open pending that, not closed on
synthetic evidence alone given this exact issue's own two-round history
of "looked fixed synthetically, broke for real."
