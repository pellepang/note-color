# Phantom in-register percussion note in chord mode: root-caused, no safe fix found (issue #75)

### Reproduction, and the original hypothesis turned out to be wrong

The same new `percussion` acoustic-test suite as #74's entry above, a
different tier: a genuinely sustained, unchanging chord (Cmaj or Am7, 4
bars, no chord change) with `BASIC_BEAT` (a plain kick/snare/hi-hat rock
beat) playing underneath. Reproduced on a fresh run (`--source loopback
--suites percussion`) post-#74: Cmaj+drums showed 8/8 kick hits
correlating with a spurious `chord_duration_tracker` finalize event
(`(pitch_class=7, octave=3)` — G3, ~196Hz — every time); Am7+drums showed
4/8. Chord-name accuracy stayed 100% throughout in both conditions (the
naming pipeline reads `chroma.fold()` directly, not
`multipitch.detect()`'s note list, so this is purely a phantom-note/
duration bug riding alongside a correctly-named chord).

Issue #75's original write-up attributed this to the kick drum's own
swept-sine decay (`synth_kick()`: 150Hz -> 45Hz exponential sweep, ~130ms)
landing near G3 during its decay tail. **That hypothesis does not survive
a timing check against the raw per-hop log.** Correlating each of the 8
Cmaj phantom-finalize timestamps against the nearest kick vs. the nearest
snare in the same window:

| phantom finalize `t` | nearest snare | offset | nearest kick | offset |
|---|---|---|---|---|
| 31.084 | 30.900 | +0.184 | 31.500 | -0.416 |
| 32.291 | 32.100 | +0.191 | 32.700 | -0.409 |
| 33.503 | 33.300 | +0.203 | 33.900 | -0.397 |
| 34.684 | 34.500 | +0.184 | 35.100 | -0.416 |
| 35.890 | 35.700 | +0.190 | 36.300 | -0.410 |
| 37.104 | 36.900 | +0.204 | 37.500 | -0.396 |
| 38.286 | 38.100 | +0.186 | 38.700 | -0.414 |
| 39.490 | 39.300 | +0.190 | 38.700 | +0.790 |

Every single event sits a tight, consistent ~184-204ms after a **snare**
hit, never near a kick. The Am7 tier's 4 events show the identical
pattern (~264-278ms after the nearest snare — a different offset than
Cmaj's, consistent with Am7's different chord content interacting
differently with harmonic pruning, but still snare-locked, never
kick-locked).

### Corrected root cause

`synth_snare()` (`scripts/acoustic_pipeline_test.py`) synthesizes a real
snare drum's two components deliberately: a broadband noise "body" (the
rattling snares) *and* "a brief low-mid tonal 'poc' component (~200Hz,
the shell/head resonance) at the attack" — a genuine, physically-modeled
feature of real snare drums, not a synthesis artifact to tune away. 200Hz
is only ~35 cents from G3 (196.00Hz) — right at the edge of
`CHORD_HARMONIC_TOLERANCE_CENTS` (35.0). `multipitch.detect()` correctly
finds this as a real spectral peak (it genuinely is one: a real sine
partial, not noise), `chord_smoother`'s `NOTE_STACK_ATTACK_HOPS` (2)
correctly promotes it into `raw_stack` after it's genuinely detected on 2
consecutive hops, and `chord_duration_tracker.update()` (fed from
`raw_stack`, always `is_onset=False` per chord mode's documented design)
correctly tracks and finalizes its ~3-4-hop lifetime as a duration event
once it decays/disappears. Every stage behaves exactly as designed on a
signal that is, from a single hop's magnitude/frequency alone,
indistinguishable from a very short real note. Confirmed directly with a
synthetic repro (no audio hardware): mixing `synth_notes()`'s
Cmaj/Am7 voicing with `synth_snare()` and sliding `multipitch.detect()`
across the snare's onset reproduces the identical `(pitch_class=7,
octave=3, freq≈197-200)` candidate, appearing 3-4 consecutive hops after
the snare's onset and nowhere near any kick.

Cmaj (C-E-G) and Am7 (A-C-E-G) both happen to already contain G as a real
chord tone — not a coincidence of picking these two test chords
specifically to dodge the collision, since the phantom's *detected*
pitch class is driven purely by the snare's own fixed 200Hz frequency,
independent of whatever chord is playing; any chord containing G (or
G#/Ab, the only other pitch class within a few dozen cents of 200Hz)
would collide the same way, and ~200Hz is a genuinely representative
real snare shell-resonance frequency, not a synthesis parameter chosen
adversarially.

### Candidate fixes evaluated, and why each was rejected

**(a) Minimum-persistence gate before chord-mode's `DurationTracker`
opens a new tracked state** (mirroring mono's
`require_onset_for_new_note`, per CLAUDE.md's own suggestion). Rejected:
the phantom's total measured lifetime here (`duration_hops` 2-5, i.e.
~45-115ms) is the same order of magnitude as the shortest *legitimate*
note this pipeline is explicitly designed to track — issue #55 story 3's
280bpm-eighth-note stress case is ~107ms/note. A threshold high enough to
reliably reject this phantom would sit right on top of, or above, that
legitimate case, with no safety margin — exactly the collision CLAUDE.md
flagged before any fix was attempted, now confirmed with the actual
numbers rather than just reasoning about it.

**(b) A magnitude/decay-shape heuristic in `multipitch.detect()` itself**
(distinguish a peak that's actively decaying within one hop's window
from a genuinely sustained partial) — the most promising-looking
candidate, so it was empirically prototyped rather than reasoned about
abstractly: for each candidate peak, split its analysis window in half
and compare FFT magnitude at that peak's frequency in the first half vs.
the second half. The phantom's ratio is indeed never close to 1.0 during
its entire life (0.01, 0.0, 1.25, 4.58, 4.88, 4.34, 5.07 across
consecutive hops — always either onset-rising or decay-falling, never
flat), which looked like a clean signature. **It is not a safe
differentiator**: the exact same experiment run against a real chord's
own genuine onset (the Cmaj control tier's attack, no drums at all) shows
an *identical*-shaped ratio trajectory for the same 3-5 hops (0.0, 0.0,
0.04, 0.66, 0.99, 1.0...) before settling near 1.0 — because a real
note's own attack transient *is* a within-window energy asymmetry, for
almost exactly as long as this phantom's entire lifetime. Any threshold
on this ratio that rejects the phantom would equally reject or delay a
real note's own onset by the same 3-5 hops, directly reopening the onset-
latency concerns issue #40/#55 already balanced carefully, and compounds
with issue #70's already-documented onset-timing fragility. Disproven
empirically, not just suspected.

**(c) Treating a close near-subharmonic match as prunable in
`multipitch._is_harmonic_of()`/pruning** (i.e. catch "196Hz is
suspiciously close to half of the real 392Hz chord tone" the same way
harmonic-consistency pruning already catches overtones). Checked the
actual cents math for this exact case: `_is_harmonic_of(392, 200, ...)`
computes harmonic_number=2, predicted=400, and
`1200*log2(392/400) ≈ -34.9` cents — already *inside* the existing 35.0
cent tolerance, i.e. this candidate sits right at the tolerance's edge by
construction, not comfortably outside it. Whether G4 (the real chord
tone) gets pruned as a harmonic of the phantom G3 or not on any given
hop is therefore already a coin flip driven by sub-Hz frequency-estimate
noise — confirmed by the raw log itself, where G4 and phantom-G3 are
seen coexisting in `note_stack` on some hops. Deliberately tightening
this further (or adding a reverse/subharmonic-direction check) is exactly
the kind of order-dependent, tolerance-boundary fragility issue #67 fixed
once already and the harmonic_number<=4 residual limitation (CLAUDE.md's
Known limitations) already documents for a structurally identical
near-miss case; doing so risks reopening #67/#68's evaluation-order
correctness for a marginal, unproven benefit here.

**(d) An amplitude/confidence threshold** (only trust a candidate loud
enough relative to the chord) was also considered and rejected without a
separate prototype: the raw per-hop confidence values captured during
the same synthetic repro show the phantom hitting `confidence=1.0` (the
single loudest peak that hop) on several hops, while the real chord
tones' confidence *drops* in relative terms during the same window
(0.66-0.99) — a real snare hit is often genuinely louder than a sustained
chord underneath it in absolute terms, so there is no magnitude-based
line to draw either.

### Conclusion: no safe fix found, left open

All three concretely-evaluated fix directions either have no safety
margin against a legitimate, already-documented use case (a) or are
empirically indistinguishable from a real note's own onset (b), or are
already known-fragile tolerance-boundary territory this codebase has
explicitly chosen not to keep tightening (c/d). Per this repo's own
established convention for this situation (see the harmonic_number<=4
residual limitation, and issue #70's onset-timing residual), this is
recorded as a known limitation rather than forced into an unsafe fix:
**a real percussion instrument's own attack-transient tonal content
(a drum shell/head resonance, a cymbal's strike fundamental, etc.) can
coincidentally land close enough to a real pitch class to be
indistinguishable, on a single hop's or even a few consecutive hops'
magnitude/frequency evidence alone, from a very short genuine note.**
Resolving this fully would need information no single hop or short hop
run can supply on its own — e.g. cross-referencing a full onset-detection
transient classifier against the broadband/noise-body component
`synth_snare()`/`synth_kick()` already model separately from their tonal
components, which is a materially bigger feature than this issue's scope,
not a targeted bug fix. Issue #75 is left open with this investigation
recorded rather than closed, so a future, better-scoped attempt (e.g.
alongside a real onset/transient classifier, if one is ever built for
other reasons) has this groundwork rather than starting over.

### Second round: two more angles tried, same conclusion

A follow-up pass looked for a genuinely different signal from the three
already-rejected candidates above -- not a re-attempt of (a)/(b)/(c), but
two new mechanisms neither of which this codebase had tried yet.

**(e) A persistence gate scoped narrowly to "duplicate pitch class,
different octave, while another octave of that pitch class is already an
active `DurationTracker` state"** -- i.e. not (a)'s blanket minimum-
persistence-before-opening-any-new-state gate, but one that only engages
for the exact shape the phantom always has (a new key whose pitch class
duplicates an already-long-sustained different-octave key). This is
purely internal to `DurationTracker` (it already knows every currently
active key's pitch class), so it was cheap to prototype: a pending-state
buffer that accumulates peak/last magnitude across a grace window and,
if the candidate survives `duplicate_pc_grace_hops` consecutive hops,
opens a normal tracked state backdated to when it first appeared (so a
note that does survive the grace window keeps its true measured
duration, not a truncated one) -- otherwise the candidate is dropped with
no finalize event at all.

Prototyped and tested in full isolation from multipitch/chord_smoother
(directly against `DurationTracker.update()` with hand-built per-hop note
sequences, to remove the harmonic-pruning coin-flip noise a full pipeline
run adds -- see below) with two matched scenarios: a "phantom" candidate
present for exactly N consecutive hops then gone, and a "genuine" candidate
present for the *same* N hops then gone (a player releasing a real short
note, not a decaying transient). **Both scenarios produced byte-identical
suppression behavior at every grace value tried (4-8 hops):** whichever
grace threshold reliably suppressed the phantom (grace > its ~5-hop
observed max lifetime) also completely suppressed the genuine same-length
note, with zero finalize event either way -- not delayed, not
under-measured, simply never reported at all. This is the same
information-theoretic wall (a) already hit, just relocated to a narrower
input class: duration/persistence alone cannot distinguish "short because
it's a transient" from "short because the player played it briefly" no
matter how the gate is scoped, because both produce the exact same
`DurationTracker` input shape. Narrowing the gate's blast radius from "any
new note" to "duplicate-pitch-class new notes only" doesn't buy safety --
it only shrinks which real notes get silently dropped, and duplicate-
pitch-class-different-octave notes are not a rare or contrived case to
sacrifice: octave-doubling (playing the same pitch class in two registers
at once, or adding a doubled note shortly after the first) is one of the
most common real voicing techniques on piano and guitar alike, arguably
*more* common than the phantom scenario this would be trading against.
Rejected on the same evidence standard as (b) -- disproven empirically
with a matched control, not just reasoned away. A full-pipeline run of
this same prototype (through real `multipitch.detect()`/`chord_smoother`
output, not hand-built sequences) additionally surfaced a second,
independent confound worth recording: introducing a genuine new
lower-octave note can itself intermittently knock the *existing* higher-
octave note out of `multipitch.detect()`'s harmonic-pruning acceptance
(the note ordering shifts once a second candidate near a harmonic-of-2
relationship is present), reproducing (c)'s already-documented coin-flip
independently of any duration-tracker change -- consistent with, not a
new instance beyond, what (c) already found.

**(f) Spectral breadth of the chroma novelty at the candidate's onset
hop** -- distinct from (b) (which looked at one peak's *temporal* shape
within its own analysis window): this instead asks whether a percussive
transient's broadband energy shows up as a wide spread of simultaneously-
rising `chroma.fold()` bins (many of the 12 pitch classes jumping at
once, since noise has no privileged pitch class) versus a real note's
attack concentrating its novelty into one or two bins (its own pitch
class plus close harmonics). Tested directly: computed `chroma.fold()`
frame-to-frame flux and its per-hop "how many of the 12 bins moved
together" spread across three synthesized scenarios -- the documented
snare-hit phantom, a genuine full chord attack from silence, and a
genuine single new note added to an already-sustained chord. All three
showed the same noisy, overlapping pattern: hops where all 12 bins moved
together happened in *every* scenario, including the two genuine-note
ones (a real chord's own onset, and a real added note's onset, both
routinely lit up most or all 12 chroma bins on at least one hop each).
Root cause is `fold()`'s own already-documented harmonic-summing bleed
(issue #56's docstring in `chord_smoother.py`): a single real note's 2nd-
4th harmonics land across several *other* pitch-class bins by design, so
a genuine attack is already spectrally "broadband" at this 12-bin,
single-hop resolution -- there isn't a clean narrowband/broadband line to
draw here either, for essentially the same reason (b) found no clean
within-window-shape line: a handful of hops of coarse aggregate spectral
evidence just isn't enough resolution to separate "a real note's own
onset mechanics" from "an unrelated transient's broadband onset,"
regardless of which axis (temporal shape, spectral breadth, or now
duration/persistence) is used to look for the difference.

A third angle was considered but not separately prototyped, since the
reasoning is conclusive without needing a run: **scaling any persistence
gate to the *live tempo estimate* instead of a fixed hop count** (i.e.
require some fraction of a beat rather than a fixed ms/hop threshold,
so the gate tightens automatically at faster tempos where legitimate
notes are also shorter). This does not resolve the underlying conflict,
it just relocates where the two curves cross: the phantom's real-world
duration is governed by drum-transient physics (the shell/head
resonance's own decay time, tens of ms, tempo-independent), while a
legitimate short note's duration is tempo-proportional by construction.
At a slow tempo both curves sit comfortably apart (a phantom is short in
absolute terms, a legitimate note is long in absolute terms) and a fixed
gate already works fine there; at a fast tempo -- exactly where a real
song is more likely to have both a driving beat *and* fast passages
played over it -- a tempo-relative gate shrinks in lockstep with
legitimate note length, converging on the same phantom-sized duration
it's trying to reject. A tempo-relative gate is not more discriminating
than a fixed one, it just makes the failure mode tempo-dependent instead
of a flat constant.

Conclusion unchanged from the first round: no signal available at the
`DurationTracker`/`multipitch`/`chroma` layer -- duration, temporal decay
shape, spectral breadth, or tempo-relative scaling of any of the above --
can separate this phantom from a legitimate short note, because both
produce indistinguishable input at every one of those layers. Left open,
same as before; the groundwork above (particularly (e) and (f)'s
prototypes, both matched-control-tested rather than merely reasoned
about) is recorded so a future attempt doesn't re-spend effort
re-discovering that these two don't work either.
