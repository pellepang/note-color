# 67. The Mod Envelope, broader destinations, and the modulation-cycle refusal (issue #208 stage 2 of 3, issue #225)

Stage 2 of the modulation layer decision 66 opened. Builds the Mod
Envelope decision 66 §5 and #208's settlement predicted, wires the rest of
the settlement's named destinations, and resolves #225 (a modulation-only
cycle raising `CycleError` instead of refusing with a sentence), which
stage 1 flagged and deliberately left open.

## 1. `ModEnvelope`: unipolar, no amount knob, per-note only

A sibling of `AmpEnvelope`, not a retrofit -- `modules/envelope.py`'s own
docstring predicted this exactly, and both classes now live in that one
file, sharing `_Spec` and `synth_engine.DahdsrEnvelope` (decision 56 §7:
imported, not copied). `AmpEnvelope` is untouched.

Two design questions were settled against the code rather than argued from
first principles:

- **Unipolar (0..1), not bipolar.** `DahdsrEnvelope.block_into()` already
  walks 0 up to `sustain` and back down to 0 -- an ordinary envelope shape
  that is never negative. A bipolar variant would need a nonzero rest
  position (the shape has no natural "middle" the way an LFO's sine does),
  which turns "at rest" into a number a patch note has to remember instead
  of the silence/zero it already reads as. This is also the conventional
  analog synth answer: a filter envelope opens a filter from wherever it
  is sitting; it does not swing through it.
- **No amount/polarity knob on the module.** Decision 66 §4 already put
  depth on the cable -- bipolar, -1..+1, a fraction of the destination's
  own range, edited at the destination knob's ring (#210 §4). A second
  amount/polarity control on the envelope itself would duplicate that
  exact number in a second place. A negative sweep (an envelope that
  *closes* a filter rather than opening it) is what a negative cable depth
  already gives for free.

**Per-note only, no mode switch**, unlike the LFO: a DAHDSR is inherently
keyed to a note's `gate`, and a once-only instance has no note to key off,
so there is no "global Mod Envelope" the way there is a global LFO.
`descriptor().poly` is unconditionally `POLY_PER_NOTE`.

**Does not end the note.** Decision 61 §4 gives `AmpEnvelope` alone the
job of setting `note.finished`; `ModEnvelope.process()` never touches the
field, even once its own envelope's `finished` flag goes true internally.
A patch with a Mod Envelope and no Amp Envelope drones exactly as one with
no envelope at all always has -- this module gives a shape to modulate
with, not a lifespan. Tested directly:
`tests/test_synth_graph_mod_stage2.py::test_never_sets_note_finished` and
its `test_amp_envelope_still_ends_the_note` regression check.

Parameters are the same six DAHDSR knobs `AmpEnvelope` has, plus its
`velocity` sensitivity knob (the envelope's own peak scaled by how hard
the note was played -- distinct from, and not a replacement for, the
cable's depth).

## 2. Broadening the destination list

Stage 1 named cutoff/resonance and level/fine as the destinations it
needed to prove the path; decision 56 §5's settlement (and #208's own
open-questions list) named the rest: filter key tracking, oscillator
pulse width, noise level, and delay time/feedback/damping/mix. All but one
are now wired, using the same chunked (`config.SYNTH_CONTROL_SUB_BLOCK`)
or elementwise-buffer approach stage 1 established -- no second pattern.

**A gap stage 1 left, found and closed: `filter.py`'s `key_tracking`.**
`ParamSpec.modulatable` defaults `True`, and `key_tracking` never
overrode it -- so `judge_modulation()` was already *accepting* a mod
cable there in stage 1, but `process()` never read the resulting buffer.
The cable connected, the ring UI (once built) would show it live, and
nothing would happen: an accepted, silent no-op, which is a worse failure
mode than a refusal, because nothing on the canvas says so. It is now
folded into the same chunked path `cutoff`/`resonance` already use.

**Straightforward buffer reads** (no chunking needed, because each enters
its module's arithmetic as a plain elementwise scale or offset already):
oscillator `pulse_width` (the phase-shift subtract and the pulse's DC
correction), noise `level` (the same shape `oscillator.py`'s `level`
already has), and delay `feedback`/`damping`/`mix` on both `Delay` and
`ShortDelay` (each a per-sample scale inside the ring's read/write or the
dry/wet blend). `Delay.reset()`/damping's own zero-check are unaffected;
each buffer path gets its own cheap "was every sample of this block
actually silent/off" short-circuit, matching the one `oscillator.py`'s
`level` already had.

**Left explicitly out, and refused rather than silently ignored: delay
`time`, on both `Delay` and `ShortDelay`.** Both now declare
`modulatable=False` on it. The reasoning, in full, lives in `delay.py`'s
module docstring; the short version: `time` is not read the way the other
three are. It sets a *read offset* into the ring, computed once a block,
and both modules read one contiguous window at that fixed offset. A live
modulation buffer would need a different offset *per sample* within the
block -- exactly what a chorus or flanger needs, and modulating delay time
is named directly in #208's settlement as "a feature, not an artefact" --
but doing it without crackle needs a per-sample interpolated (fractional)
ring read, the same kind of gather-and-interpolate `oscillator.py`'s
`_read_into()` already does for its wavetable, layered on top of the
delay's own read-stays-behind-write invariant as that offset moves. That
is a rewrite of the read path and its invariants on both modules, not a
knob-level change like the other three, and attempting a partial version
that still crackles would be worse than the honest refusal: a cable that
visibly does not attach, with a sentence saying why, rather than one that
connects and buzzes. Named here as follow-up work rather than opened as a
new ticket, since the owner has not asked for one yet; `short_delay.py` is
the natural module to grow it in, since it already reads sub-block and is
the one actually shaped for a chorus/flanger use.

Respected without change: `ParamSpec.modulatable=False` on oscillator
`octave`/`semitones` and on `filter.type`/`noise.colour` -- stepped,
whole-instrument choices, not sweepable values.

## 3. Issue #225: a modulation-only cycle now refuses with a `Verdict`

Stage 1 folded modulation edges into `ModuleGraph._ordering_edges()`
(so `order()`/`compile()` already saw a mod-only cycle and raised
`CycleError` rather than silently running something wrong) but never gave
`judge_modulation()` the audio side's `_cycle_verdict()` treatment. That
gap was moot in stage 1 -- its own destination list could not reach one --
but stage 2's own broadened set makes one trivially reachable: the LFO's
own `rate` parameter has been `modulatable=True` since stage 1 (never
overridden), so two LFOs modulating each other's rate
(`A.mod -> B.rate`, `B.mod -> A.rate`) already close a cycle, and nothing
about Mod Envelope or the wider destination list is required to hit it --
broadening the set only makes it more likely a real patch stumbles into
one by accident, which is why #225 called this out as more urgent now.

**The open sub-question #225 posed, settled:** does a modulation cycle
through a module with `block_delay >= 1` become legal on the same
reasoning decision 56 §4 gives audio cycles? **Yes, on the same
reasoning -- but the reasoning is presently unreachable, so in practice
every modulation cycle is refused.** Bitwig's rule that decision 56 §4
already established is not actually about audio specifically: it is about
whether a module *guarantees* its output this block was computed from
input read a block ago, which is a fact about the module's own
`process()`, independent of which port kind carries the guarantee. A
hypothetical modulation-delay module (a `PORT_MOD` output, `block_delay`
of at least 1) would legalise a mod-only loop through it for the identical
reason `Delay` legalises an audio one. No module emitting `PORT_MOD`
reports `block_delay >= 1` today -- neither the LFO nor `ModEnvelope`, and
nothing else has a mod output at all -- so this is presently dead code
rather than reachable, and the honest current behaviour is exactly what
#225 guessed it would be: every modulation cycle is refused. The moment a
future module changes that fact, `ModuleGraph._mod_cycle_verdict()` and
the matching exception in `_ordering_edges()` already know what to do
with it; nothing about this decision needs revisiting when that day
comes, only a module.

`judge_modulation()` now calls `_mod_cycle_verdict()` as its last check,
the modulation-side twin of `judge()`'s `_cycle_verdict()`: the same
`_ordering_path()` DFS, the same code (`REFUSE_CYCLE`), and a sentence in
decision 60's grammar naming the loop back to the person holding the
cable, e.g. *"That closes a modulation loop with no Delay in it (LFO A →
LFO B). A modulation source cannot be tugged by something its own signal
already reaches -- there is no Delay module for modulation to come back
through a block later, the way an audio feedback loop can."* A cable
built past `judge()` on purpose (`force_connect_modulation()`, for tests
exercising `compile()`'s own defences) can still reach `CycleError` --
that path was never meant to go through a `Verdict` and still does not;
`test_force_built_mod_cycle_still_compiles_without_crashing` in
`tests/test_synth_graph_mod_stage2.py` pins that down explicitly so the
distinction does not blur later.

## Measured cost, against the 70% trigger

512 frames / 44100 Hz = 11.61ms budget, at 16 voices, same methodology
decision 66 and decision 65 used (wall-clock around `PolyGraph.process()`,
`sounddevice` not involved). This run's machine was under real contention
while measuring (`uptime` showed a load average of 8.4 on 4 cores --
another agent's concurrent work in this repo, plus the desktop's own
load) and the raw numbers below run consistently higher than decision
66's own recorded baseline for *identical, unmodified* code:

| patch | this run, ms/block | % of budget |
|---|---|---|
| baseline (osc → filter → amp env → mix), 16 voices | 2.88 | 24.8% |
| + LFO → cutoff (stage 1's own shape) | 7.25 | 62.5% |
| + stage 2's wide destination set + Mod Envelope | 13.59 | **117.1%** |

Read literally, the wide-patch row blows past decision 55's 70% trigger.
Read against decision 66's own calibration it does not, and the
calibration is worth doing explicitly rather than reporting either number
alone: this run's baseline (2.88ms) is 1.88x decision 66's recorded
baseline (1.53ms) for the *same unmodified baseline patch shape*, which
is not something stage 2 touched -- so the honest read is that this
machine, right now, runs the whole engine about 1.9x slower than decision
66's measurement environment did, not that anything got 1.9x more
expensive. The stage-1-shape row's ratio to this run's own baseline
(7.25 / 2.88 = 2.52x) matches decision 66's own recorded ratio for the
identical patch (3.88 / 1.53 = 2.54x) to within 1% -- strong evidence the
*relative* cost of the modulation machinery is unchanged from stage 1,
and that the discrepancy really is environmental rather than a regression
stage 2 introduced.

Applying stage 2's own measured ratio (13.59 / 2.88 = 4.72x baseline) to
decision 66's calibrated baseline (1.53ms) projects **1.53 x 4.72 =
7.22ms, 62.2% of budget** for the full wide-destination-plus-Mod-Envelope
patch on a machine running at decision 66's own speed -- under the 70%
trigger, but close enough to it that it is flagged here rather than
declared safely clear. Two things worth naming rather than leaving
implicit:

- **This patch modulates every newly-wired destination at once** (filter
  cutoff, resonance, key tracking; oscillator pulse width and fine; noise
  level; delay feedback and mix), which is a stress test of "how expensive
  can this get", not a claim about what a typical patch does. A patch
  modulating one or two of these costs close to the stage-1-shape row.
- **Re-measurement on a quiet machine is worth doing before this ships**,
  precisely because 62% is close enough to 70% that contention noise could
  tip a real reading either side of the trigger. This decision does not
  block on that remeasurement -- nothing here is irreversible, and the
  chunked/elementwise approach is the same one already proved in stage 1
  -- but it is named so the next agent (or the owner, on real hardware)
  does not have to rediscover the calibration method to get a trustworthy
  number.

No allocation beyond what stage 1 and `filter.py`'s own documented
`lfilter` departure already accounted for:
`tests/test_synth_graph_mod_stage2.py::test_process_allocates_nothing_extra_with_the_wide_patch_patched`
extends stage 1's proof to a patch carrying the Mod Envelope and every
newly-wired destination at once.

## What fought the spec, and what is left open

- **Nothing under `gui/`.** `patch_bridge.py`'s `MODULE_FACTORIES` and
  `NOT_IN_ENGINE` were only read, never edited -- they belong to the
  parallel agent wiring the canvas to the modulation engine this round.
  Adding `"mod_env"` to `MODULE_FACTORIES` (and removing `"lfo"`/adding a
  `mod_env` type key to whatever list currently keeps modulation sources
  out of the drawer) is the concrete change the canvas side needs to make
  `ModEnvelope` reachable from the Synth View; flagged for that agent
  rather than made here.
- **Delay `time` modulation** is the one named destination this stage
  does not wire, for the reasons in §2 above -- recorded as follow-up
  work rather than solved partially.
- **The 62% projected cost** is close enough to decision 55's 70% trigger
  that it is worth a clean re-measurement before the wide destination set
  is considered fully settled; see "Measured cost" above.
- **Nothing here could be verified without a real audio device**, and
  nothing needed to be -- every claim is measured as an array property
  (unipolar range, buffer-vs-scalar divergence, finiteness, allocation),
  the same convention every graph test in this stream already uses.

## Index

See `docs/DECISIONS.md`.
