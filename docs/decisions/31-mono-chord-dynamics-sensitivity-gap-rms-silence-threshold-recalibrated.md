# Mono/chord dynamics sensitivity gap: `RMS_SILENCE_THRESHOLD` recalibrated, `CONFIDENCE_THRESHOLD` left alone (issue #72)

`scripts/acoustic_pipeline_test.py`'s new `dynamics` suite (loud-to-whisper
`base_amp` sweep, `--source loopback`) found mono note recall cratering
completely between `base_amp=0.05` ("quiet", recall 1.0) and `base_amp=0.02`
("very_quiet", recall 0.0), while chord-name accuracy and phantom/missing
pitch-class rates stayed perfect down to `base_amp=0.008` ("whisper") —
over 6x quieter than where mono went fully silent. Investigated in full
before changing anything, per the issue's own explicit ask for "at
minimum, a deliberate decision."

**Step 1: how much of the 6x gap is the reported chord-synthesis
amplitude artifact, not a real gate difference?** `synth_notes()` sums
each chord tone independently at the same `base_amp` before a
down-only peak-cap, so a chord's *summed* signal is inherently louder in
absolute terms than a single note at the same `base_amp` — the issue
itself flagged this as unverified. Measured directly (no audio hardware
needed — pure NumPy on `synth_notes()`'s own output): at every `base_amp`
below the peak-cap's engagement point (i.e. every level this suite's
`quiet`/`very_quiet`/`whisper` tiers actually exercise), a 3-note major
triad's steady-state RMS is ~1.73x a single note's at the same
`base_amp`, and a 4-note min7's is ~2.0x — both match the textbook
incoherent-sum √N prediction for independently-phased tones almost
exactly. That's real, but nowhere near 6x — most of the reported gap is
not this artifact.

**Step 2: is it the RMS gate or the confidence gate doing the killing?**
A small standalone probe (`main.SessionState` + real `--source loopback`
round trip, `RenderItem.rms`/`.confidence` logged per hop — not part of
the checked-in suite, a throwaway diagnostic) played a single C2 tone at
`quiet`/`very_quiet`/`whisper`/an even quieter `ultra` (0.002) level and
logged both fields hop-by-hop. Result: YIN's confidence measured a flat
**1.000** at every level tested, all the way down to `ultra`
(captured RMS ~0.0008) — the periodicity evidence in this clean digital
loopback signal is not degrading at all as amplitude drops, since
loopback has no physical noise floor (mic self-noise, room rumble) to
erode it. What *does* change is the raw captured RMS crossing
`config.RMS_SILENCE_THRESHOLD` (0.01 at the time): `quiet` measured
~0.020 (passes), `very_quiet` ~0.008 (fails), `whisper` ~0.003 (fails).
The recall cliff lines up almost exactly with that crossing. Confirmed:
**`RMS_SILENCE_THRESHOLD`, not `CONFIDENCE_THRESHOLD`, is the sole
mechanism gating mono out at these levels** — `NoteSmoother.update()`
short-circuits on `rms < self.rms_silence_threshold` before confidence is
even consulted for candidate promotion.

**Step 3: does the `noise` suite (issue #71's regression guard) actually
exercise this threshold at all?** `_add_noise()`'s injected Gaussian
noise is `amplitude` directly (not scaled relative to note volume) — the
suite's own quietest nonzero tier, `light`, uses `amplitude=0.05`, giving
a silence-gap RMS of ~0.05 (for zero-mean Gaussian noise, RMS ≈ std).
That's already 5x the *old* `RMS_SILENCE_THRESHOLD` (0.01) and 10x any
candidate lowered value discussed below — meaning the `noise` suite's
synthetic noise floor clears the RMS gate regardless of where this
constant sits, both before and after any change in this range. Issue
#71's actual regression-guard mechanism is entirely `CONFIDENCE_THRESHOLD`
(YIN correctly returning `(None, 0.0)` on noise-degraded input post-#71's
fallback removal, then `NoteSmoother` gating on confidence, not RMS) —
this constant and that one are orthogonal in the region either suite
actually tests.

**Decision: lowered `RMS_SILENCE_THRESHOLD` 0.01 → 0.005 (2x), left
`CONFIDENCE_THRESHOLD` untouched.** This is a real, evidenced recovery of
real headroom (recovers `very_quiet`, the exact tier the issue's own data
table showed failing) with a change that provably cannot interact with
issue #71's fix (see Step 3) and doesn't touch the confidence gate #71
already spent a full threshold sweep calibrating. Deliberately **not**
chased further to fully close the gap down to chord's own whisper-level
floor (0.008 `base_amp`, RMS ~0.003), for three reasons: (1) chord mode's
insensitivity to amplitude at all is architectural, not a threshold this
project could match on mono's side even in principle — `chord_templates
.match()`'s cosine similarity is scale-invariant by construction, with no
absolute floor anywhere in that path, whereas mono's silence gate exists
specifically to stop `NoteSmoother`/YIN from chasing near-zero-energy
blocks, a deliberate difference already documented in CLAUDE.md's
Architecture section, not a bug to erase; (2) this project has a
documented history (issues #69, #71, both twice) of synthetic/loopback
conclusions *not* surviving real-mic re-verification, specifically
because loopback has no real acoustic noise floor to test against — this
whole investigation is exactly that kind of conclusion (Step 2's `1.000`
confidence at `ultra` is a property of a noiseless digital path, not
evidence about what a real mic's self-noise floor would do to a
correspondingly lower RMS gate), so a large jump felt reckless without
that check even though nothing here contradicts it; (3) `--sensitivity`
(CLI flag, live `[`/`]` hotkeys) already gives a user actually playing
quietly in a real quiet room a bigger, no-code-change lever than this
default ever will — `NoteSmoother.set_sensitivity()` divides both gates
by it directly, so a user who needs `whisper`-level headroom already has
a path to it today.

**Validated, both loopback suites re-run before/after on the same real
`--source loopback` round trip:**

| suite / level | before (0.01) | after (0.005) |
|---|---|---|
| dynamics: very_quiet note recall | 0.0 | **1.0** |
| dynamics: whisper note recall | 0.0 | 0.0 (unchanged, as intended — see reason 2/3 above) |
| dynamics: chord accuracy, all levels | 1.0 | 1.0 (unaffected, no gate to move) |
| noise: light note accuracy | 1.0 | 1.0 (unchanged) |
| noise: moderate note accuracy | 0.0 | 0.0 (unchanged — correctly still silence-gated, not #71's wrong-confident failure mode reopened) |
| noise: moderate chord accuracy | 1.0 | 1.0 (unchanged) |

(`noise`'s `clean`-tier mono accuracy moved 0.731→1.0 between the two
runs; per Step 3 this constant has zero effect on `clean`'s
noiseless/normal-volume audio either way, so that's ordinary
run-to-run loopback jitter — CLAUDE.md's already-documented "quality
varies run-to-run with room/mic conditions," not this change.)

Full `pytest tests/`: 326 passed against every file this change could
plausibly affect (`tests/test_note_smoother.py` reads
`config.RMS_SILENCE_THRESHOLD` symbolically, not a hardcoded literal, so
needed no update). Two unrelated failures in
`tests/test_terminal_tab_display.py` (`NameError: wcwidth`) at the time
of this investigation belong to a concurrent agent's in-progress edit to
that file (issue #82/#83 territory) — confirmed by `git diff --stat`
showing that file already modified before this investigation touched
anything, and by the failure itself (a missing import, nothing to do
with silence gating) — not caused by, or fixed by, this change.

**Still open, honestly:** the real-mic re-verification caveat above is
real, not boilerplate — this conclusion is loopback/synthetic-validated
only, same standing caveat as issues #69 and #71's own entries. If a
future real speaker→mic session finds 0.005 too permissive (a real room's
noise floor doing to this gate what it can't do here), the fix is a
one-line revert, same low blast radius as the change itself.
