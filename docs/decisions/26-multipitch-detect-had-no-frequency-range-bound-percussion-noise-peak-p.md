# `multipitch.detect()` had no frequency-range bound: percussion noise peak-picked as phantom notes at nonsensical octaves (issue #74)

### Root cause

A new acoustic-test suite (`percussion`, `scripts/acoustic_pipeline_test.py`'s
`build_percussion()`/`analyze_percussion()`) plays synthesized kick/snare/
hi-hat percussion — broadband/inharmonic, no stable pitch by construction
— through the real live pipeline via `--source loopback`. With **zero
pitched content playing at all**, the polyphonic chord-mode pipeline
(`multipitch.detect()` + `chord_smoother.py` + `chord_templates.match()`)
frequently produced a non-empty `note_stack`, and sometimes even a
confidently-named chord.

`multipitch.detect()` converts *every* spectral peak surviving
`min_mag_ratio` straight to a MIDI pitch class/octave via
`midi = 69 + 12*np.log2(freq/440.0)`, with **no frequency-range bound at
all** — unlike the monophonic path, `pitch_detect.detect_pitch()` (YIN) is
explicitly bounded by `config.FMIN`/`FMAX` (65-1000Hz, ~C2-B5, this app's
whole targeted instrument range; see `tau_min`/`tau_max`'s derivation from
`fmax`/`fmin` in `pitch_detect.py`). A hi-hat's high-passed noise (6-11kHz
in this test's synthesis) is ~3-4 octaves above `FMAX`, but multipitch
happily reported it as notes at octave 8-9 — nonsensical for any real
instrument this app targets. Confirmed directly (no audio hardware
needed) by feeding `multipitch.detect()` a synthetic spectrum with energy
only above 4kHz (band-passed white noise, `np.fft.rfft`/`irfft` with
everything below 4kHz zeroed): it reported 6 phantom "notes", all at
octave 8-9, e.g. `NoteCandidate(pitch_class=10, octave=8, freq=7360.1,
confidence=1.0)`. Root cause is a plain missing bound, not a subtler
peak-picking or pruning bug — no percussion/pitch-plausibility classifier
exists anywhere in this pipeline at all (per CLAUDE.md's architecture,
chroma/multipitch always run every hop regardless of what's actually
playing).

Reproduced on the real acoustic suite twice (independent runs, same
machine/session) before the fix, numbers consistent both times:

| tier (no pitched content at all) | hops | false-chord rate | false note-stack rate |
|---|---|---|---|
| isolated_hits (run 1 / run 2) | 194 / 193 | 0.0206 / 0.0 | 0.2371 / 0.2435 |
| beat_only, sustained 4-bar beat (run 1 / run 2) | 465 / 465 | 0.1376 / 0.1312 | 0.6774 / 0.6688 |

So a sustained drum-only beat with **no pitched content whatsoever**
showed a non-empty (phantom) note_stack on ~67-68% of hops, and a
fully-formed, "confident" chord name (e.g. `B13b9`, `AΔ9`, `Ab°Δ7/D`) on
~13-14% of hops. Per-drum-type breakdown on isolated single hits: kick
and snare produced phantom note_stacks with plausible-*register* pitch
classes (kick: e.g. `(7,2)`/`(10,1)`, near its own low thump; snare:
mixed low+high). Hi-hat's phantom notes were exclusively at octave 8-9
— purely the missing upper-bound artifact — and hi-hat was also the only
drum type among isolated hits that produced a "confident" chord name.

### Fix

`multipitch.detect()` gained `min_freq_hz`/`max_freq_hz` parameters
(defaulting to new module constants `DEFAULT_MIN_FREQ_HZ`/
`DEFAULT_MAX_FREQ_HZ`, 65.0/1000.0 — numerically identical to
`config.FMIN`/`FMAX`, same "own DEFAULT_* mirrors config's real value,
callers pass the config value explicitly" convention this module's
`DEFAULT_MAX_NOTES`/`DEFAULT_MIN_MAG_RATIO`/etc. already use for
`config.CHORD_MAX_NOTES`/`CHORD_PEAK_MIN_MAG_RATIO`/etc.). Filtering
happens in the raw-candidate-building loop, immediately after each peak's
quadratic-interpolated frequency is computed and before either the
`max_peak_candidates` cap or the ascending-frequency harmonic-pruning
walk (issue #67) ever see it:

```python
freq = (i + offset) * sample_rate / fft_size
if freq < min_freq_hz or freq > max_freq_hz:
    continue
raw_candidates.append((freq, magnitude[i]))
```

`main.py`'s `analysis_loop()` and `batch_transcribe.py`'s `transcribe()`
(the only two real call sites) now pass `min_freq_hz=config.FMIN,
max_freq_hz=config.FMAX` explicitly, exactly the same pattern they
already use for `config.CHORD_MAX_NOTES`/`CHORD_PEAK_MIN_MAG_RATIO`/etc.

**Why reuse `config.FMIN`/`FMAX` rather than a separate polyphonic-only
range?** Considered and rejected: multipitch is still detecting notes
from the same real instruments/register this whole app targets, just
more than one at a time — there is no principled reason a chord's
individual notes would plausibly sit outside the range a single melody
note already can't. A polyphonic voicing's upper extensions (9ths,
11ths, 13ths) could in principle push a chord tone's *own* fundamental
higher than a plain melody line would — checked directly against this
app's own acoustic test fixtures (`scripts/acoustic_pipeline_test.py`'s
`voice_chord()`, every quality including `add9`/`sus2`/`sus4`, registers
3-4) and every tested chord tone's fundamental already sits well under
1000Hz; the existing unit-test suite's own dense/high voicings
(`test_dense_six_note_chord_all_survive_when_not_harmonically_colliding()`'s
6-note stack up to C#4, `test_high_order_harmonic_near_miss_does_not_
prune_a_real_independent_note()`'s B5) also all clear comfortably. A
harmonic *overtone* of an in-range fundamental (a real note's 3rd/4th
partial) routinely does land above 1000Hz — e.g. a sus4 voicing's F4/G4
partials land at 1047Hz/1176Hz — but those were already being correctly
discarded via harmonic-consistency pruning before this fix (confirmed by
direct A/B: an isolated synthetic Csus4 chord — C4+F4+G4, harmonics 1-4
weighted like `chroma.HARMONIC_WEIGHTS` — produces the identical 3-note
`NoteCandidate` list whether `min_freq_hz`/`max_freq_hz` are 65/1000 or
0/1e9), so pre-filtering them earlier is a no-op for correctness, just a
few fewer candidates walked through pruning.

### Verification, including a same-day false alarm

Unit tests added to `tests/test_multipitch.py`:
`test_out_of_range_high_frequency_noise_produces_no_phantom_notes()`
(band-passed white noise above 4kHz -> `[]`, the direct repro above),
`test_out_of_range_low_frequency_rumble_produces_no_phantom_notes()`
(a companion low-end case — a kick drum's sub-bass thump can sit below
`FMIN` entirely, not just above `FMAX`), and
`test_frequency_range_bound_does_not_affect_in_range_chords()` (a chord
spanning close to both edges of the default range, C2+B5, still detects
correctly). Every pre-existing `multipitch`/chord test — including the
issue #67/#68 harmonic-pruning and `CHORD_HARMONIC_MAX_NUMBER`-cap tests,
the ones most likely to be accidentally re-broken by touching this
function — passed unchanged. Full suite green throughout.

Re-running the percussion suite after the fix (`--source loopback`):

| tier | false-chord rate (before -> after) | false note-stack rate (before -> after) |
|---|---|---|
| isolated_hits | 0.0-0.0206 -> 0.0 | 0.2371-0.2435 -> 0.0825-0.0928 |
| beat_only (sustained beat) | 0.1312-0.1376 -> 0.0-0.0151 | 0.6688-0.6774 -> 0.2968-0.3032 |

The false-chord rate on sustained drums-only audio dropped to essentially
zero, as expected — that number is driven almost entirely by hi-hat's
octave-8-9 phantom notes clearing `CHORD_MATCH_THRESHOLD`, exactly what
the frequency bound removes. The false note-stack rate dropped
substantially (roughly half) but stayed nonzero: kick/snare's own
broadband energy still has real content *inside* [65, 1000]Hz (a kick's
low thump, a snare's mixed low+high spectrum), which peak-picks into a
plausible-looking but still spurious note — a real, separate gap (no
percussion/pitch classifier exists anywhere in this pipeline) that this
fix doesn't claim to close; see CLAUDE.md's Known limitations.

Tiers 2-4 (real chords/melody mixed with drums) and the `chords`/
`density` suites confirmed no regression to legitimate detection —
`chords` stayed at 100% name accuracy / 0 mean phantom pcs/hop (49/49),
`density` stayed within the existing 0-0.73 missing-pcs/0-0.33 phantom-
pcs baseline range across 1-6 simultaneous notes (see the issue #67/#68
round 2 entry above for that baseline's origin).

**False-alarm digression, recorded because it nearly became a wrong
conclusion.** A first post-fix `chords` suite run measured a startling
77.6% chord-name accuracy (down from the ~100% baseline) with a high
0.437 mean phantom-pcs/hop — on its face, a serious regression. Before
accepting that, the exact failing case (`Csus4`, register 4) was
reproduced in isolation as a static synthesized waveform (matching
`synth_notes()`'s own harmonics-1-4-weighted-like-`HARMONIC_WEIGHTS`
construction): `multipitch.detect()` returned the *identical* 3-note
result with the frequency bound on or off. That ruled out the fix itself
as the mechanism for a single chord's peak-picking. A controlled A/B
against the real live pipeline followed: `min_freq_hz`/`max_freq_hz`
temporarily forced wide open at the real `main.py` call site (`0.0`/
`1e9`, bound effectively disabled) reproduced a clean 100%/0-phantom
result; putting the real fix back (`config.FMIN`/`FMAX`) and re-running
immediately after reproduced the clean 100%/0-phantom result too. The
original bad run did not replicate — concluded to be transient
environmental noise in this particular dev session (this machine had
another long-running `virtualnote.py` process open concurrently for the
whole session, a plausible source of audio-device contention against
`--source loopback`'s own PipeWire capture), not a real code regression.
Recorded here per this codebase's own precedent (issue #69's real-mic
regression-and-recalibration round) that a single suspicious acoustic-
suite number is a lead to chase with a controlled re-run, not a
verdict to act on immediately — in this instance the re-run cleared the
fix rather than confirming a regression, which is exactly why the
re-run step matters.
