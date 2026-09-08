# YIN's unprincipled "loose global fallback" removed: it was accepting noise-driven false locks at high confidence (issue #71)

`scripts/acoustic_pipeline_test.py`'s `noise` suite (new this session: a
reduced chromatic+chord sweep with additive broadband Gaussian noise at a
few levels, layered on top of the otherwise effectively-silent-room
conditions every other suite runs under) found `pitch_detect.detect_pitch()`
doing something categorically worse than "losing confidence under noise":
at `noise` suite's `moderate` level (0.15 relative amplitude), it
**confidently** (0.6-0.9 confidence — comfortably above
`config.CONFIDENCE_THRESHOLD=0.5`) locked onto a pitch near `FMIN` (~octave
2) for *any* note being played, regardless of octave. This isn't #69's
octave-doubling (a shorter-lag harmonic winning) — for higher-register
notes the false pitch has no harmonic/subharmonic relationship to the true
note at all.

**Root cause, confirmed via synthetic reproduction (no audio hardware
needed) and re-confirmed against real `--source loopback` acoustic-test
raw hop logs.** `detect_pitch()`'s tau-selection has two branches: (1) an
ascending scan from `tau_min` accepting the first `tau` whose CMND dips
below `threshold` (config's `YIN_THRESHOLD=0.12`), correctly walking
forward to that dip's local minimum first — this branch is fine, matches
classic YIN, and was untouched by this fix; (2) a fallback, present since
this repo's very first commit, that fired whenever branch (1) found
*nothing* — it took the single global argmin of CMND across the *entire*
`[tau_min, tau_max)` search range and accepted it as a detection as long as
it beat a barely-there cutoff (`< 0.99`), reporting confidence as
`1 - cmnd[tau]` with no regard for *why* that argmin was low.

That fallback is fundamentally unprincipled: branch (1)'s scan already
checks `cmnd[t] < threshold` for *every* `t` in range individually, so if
it found nothing, there is by construction no `tau` anywhere that clears
the real detection threshold — branch (2)'s separate, ~8x-looser cutoff
(0.99 vs. 0.12) has no principled basis to exist at all. Direct inspection
of a synthesized A4 tone (harmonics 1-4 weighted like
`chroma.HARMONIC_WEIGHTS`, the exact profile `scripts/acoustic_pipeline_
test.py`'s `synth_notes()` uses) plus 0.05-0.15 amplitude white noise
showed why this matters in practice: the true fundamental's own CMND dip
(tau≈50 samples) gets shallower under noise and stops clearing 0.12, while
one of that *same period's own integer multiples* — confirmed empirically
at 2x (F#3), 5x (A4), 7x (D#5), all landing well inside `tau_max` for a
short-period upper-register note — occasionally looks deeper. This isn't
coincidence: a periodic signal's difference function has real dips not
just at its true period but at every integer multiple of it, and CMND's
own cumulative-mean normalization is *systematically* biased lower at
large tau even for **pure noise with no tone at all** — 30 trials of pure
white noise (no signal) showed every single trial's global-minimum CMND
landing in the top third of the search range (near `tau_max`, i.e. near
`FMIN`), averaging ~0.87 there vs. ~0.95-1.0 near `tau_min` — because the
difference function's window (`w - tau` samples) shrinks as tau grows, so
fewer samples back each large-tau CMND estimate, adding variance and (via
the cumulative-mean denominator lagging behind a downward-trending
numerator) a real downward bias. The two effects compound: a real note's
own large-multiple-of-tau dip, already assisted by that same near-tau_max
bias, can end up looking confidently periodic even while the true,
short-tau period is too noise-degraded to clear threshold at all.

**The fix**: delete the fallback branch entirely. When the ascending scan
finds no `tau` clearing `threshold`, `detect_pitch()` now returns
`(None, 0.0)` — the same "unvoiced frame" behavior classic YIN specifies,
and the only behavior actually justified once branch (1) has already ruled
out every candidate under the real threshold. No new constant, no
`config.py` change — the loose `0.99` cutoff this replaces was never a
named, principled constant to begin with.

**A threshold recalibration (raising `YIN_THRESHOLD` itself) was
investigated and rejected, not blindly skipped.** A sweep from 0.12 to 0.30
against the exact reproducing synthetic signals found: raising it recovers
some `noise_amp=0.05` cases via the *principled* ascending scan (since the
true, short tau is always found before the scan ever reaches a near-
`tau_max` artifact — a real, safe mechanism) — but this repo's own `light`
noise level (0.05) was *already* 100% recall/accuracy before this fix (the
real captured audio's downsampling from `PLAYBACK_SR=44100` to
`SAMPLE_RATE=22050` attenuates injected wideband noise more than the
tonal harmonics, an effective SNR gain the isolated synthetic sweep above
doesn't get), so there was no headroom to improve there. At `moderate`
(0.15), *no* threshold up to 0.30 recovered a single correct detection in
the sweep — the true fundamental's CMND is genuinely too degraded within
one ~93ms window at that SNR, a real statistical limit, not a threshold-
calibration problem. Worse, raising `YIN_THRESHOLD` also loosens issue
#69's subharmonic-check gate (`cmnd[cand_tau] < threshold` reuses the same
parameter), and reintroduced occasional wrong-confident detections at C2
(octave 2) even at `noise_amp=0.05` — i.e. it would have reopened #69's
exact failure mode at low octaves for zero measured benefit at the levels
this app's own noise suite actually tests. `YIN_THRESHOLD` was left at
0.12.

**Before/after, from real `--source loopback` acoustic hop logs** (not
just the synthetic repro — `acoustic_test_results/round2/noise_raw.json`
vs. `acoustic_test_results/yin_fallback_fix/noise_raw.json`, same
timeline/expected-note ground truth, only the code differs), classified
per steady-state hop as correct / **wrong-but-confident (>=0.5)** / wrong-
low-confidence / no-detection:

| level | correct (pre→post) | **wrong-confident (pre→post)** | no-detection (pre→post) |
|---|---|---|---|
| clean | 100%→100% | 0%→0% | 0%→0% |
| light | 100%→100% | 0%→0% | 0%→0% |
| moderate | 27.2%→0.0% | **72.8%→0.0%** | 0%→100% |

The crude "note accuracy" number at `moderate` reads the same "0-ish"
grade before conclusion either way, but that's misleading in isolation:
pre-fix, both the 27.2% "correct" and the 72.8% "wrong-confident" hops
came from the *same* unprincipled fallback mechanism landing, by chance,
on the true tau or one of its multiples respectively — neither was earned
by real periodicity evidence, so the 27.2% wasn't a real capability being
traded away. What actually matters — a live color visualizer never
silently displaying a *confidently wrong* note — went from 72.8% of
moderate-noise hops to 0%. `clean` and `light` (the app's own defined
noise levels) are both unaffected, and the `chromatic` suite (clean-signal
regression guard) stayed 100%/100%, matching every prior baseline this
session. Full `pytest tests/`: 320 passed (up from 310; 10 new adversarial
tests in `tests/test_pitch_detect.py`
(`test_broadband_noise_never_confidently_wrong`,
`test_no_subthreshold_tau_anywhere_returns_none_not_loose_fallback`),
including an explicit re-run of every #69 regression test, all still
green — this fix doesn't touch the `tau is not None` branch #69's
subharmonic check lives in at all.

**Known residual limitation, left honest rather than force-fixed.** At
sustained broadband noise around a 0.15-relative-amplitude SNR, a single
~93ms analysis window's periodicity evidence for the true note is
genuinely too degraded for *any* principled per-hop threshold to recover —
confirmed by the threshold sweep above finding zero recoverable margin up
to 0.30. `note_smoother.NoteSmoother`'s existing silence-gating is exactly
the mechanism this now correctly falls through to (same category as this
project's other documented "sometimes silence-gated under real acoustic
conditions" limitations, e.g. issue #69's own writeup) — recovering actual
detection at this SNR would need information beyond a single hop's
magnitude spectrum (e.g. cross-hop periodicity accumulation), out of this
fix's scope. This was validated via synthetic adversarial testing plus a
`--source loopback` round-trip through the real unmodified pipeline, not a
real physical speaker→mic session — this repo has a documented history
(issue #69, twice) of synthetic/loopback fixes not surviving real-mic
verification, so a real-mic re-check is still advisable before treating
this as fully closed in the field.

**Follow-up, found by the orchestrating session immediately after
landing the fix above (not by the fix's own agent, whose regression guard
only re-checked the `chromatic` suite): removing the argmin fallback also
cost real recall at the `tempo` suite's fastest tested speed.** 90/140/
200bpm all stayed 100% (unaffected — plenty of periods per analysis
window at those speeds), but 280bpm (eighth notes, 107ms/note, already
this suite's explicit "how fast can it go" stress case, not a normal-use
guarantee) dropped from a stable 88% (measured twice, both this session's
#67/#68 work and independently before the #71 fix) to a stable 71%
(likewise measured twice, immediately after #71 landed, `--source
loopback --suites tempo`, no other change in between). This is the same
trade-off as the noise case above, at a different stressor: a fast
legato transition's analysis window is briefly contaminated by the
previous/next note bleeding in at the window edges, and the old fallback
would sometimes guess through that ambiguity by luck (right or wrong,
unverifiable from the recall number alone, same as the noise case);
removing it means some of those borderline hops now correctly report no
detection instead of a lucky guess. Left as-is, not chased further: only
the most extreme tested tempo is affected, 90-200bpm are untouched, and
re-introducing any form of "guess when in doubt" would directly undo
issue #71's whole point. Documented here and in CLAUDE.md's Known
limitations rather than silently left for the next person to rediscover.
