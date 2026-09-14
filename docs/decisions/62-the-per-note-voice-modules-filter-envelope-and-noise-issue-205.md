# 62. The per-note voice modules: filter, amp envelope and noise (ticket #205, map #179)

`audio/graph/modules/filter.py`, `envelope.py`, `noise.py`. Fourth ticket of
decision 56's engine stream: enough real modules that a patch is worth
hearing, built by **porting** decision 42's engine rather than rewriting it.

## 1. Nothing new was invented, on purpose

The SVF coefficients, the `lfilter` recurrence, the DAHDSR walk, the pink
noise `B`/`A` pair and its make-up gain are all `synth_engine.py`'s, imported
and not copied -- decision 56 §7's rule that the graph engine lives *beside*
the fixed engine rather than forking it. Every one of those has a numerical
test already; a second implementation would mean a second set of them, and
the second set is always the one that drifts.

What is new is only the shape: a module reads one float64 array of knobs and
writes one buffer, and knows nothing about what is patched into it.

Three small things were added *to* `DahdsrEnvelope` rather than around it,
all arithmetic-identical to what was there:

- `restart()` -- the starting state, reusable, so `reset()` at note-on does
  not rebuild the object (`poly.PolyGraph` refuses to allocate at note-on);
- `block_into(out)` -- the same walk into a buffer the caller owns, with
  `block()` now being this plus the allocation;
- `preallocate(max_block)` -- a ramp buffer, so a ramping stage does not
  build an `arange` per block. The expression is `level + step * ramp` in the
  same order either way, which is what lets §5's parity test compare the two
  engines *bit for bit* on the first block.

`synth_engine.signal_module()` is a public way to bind `lfilter` once, at
activation, instead of reaching for `_signal()` on the audio thread.

## 2. The envelope is what ends a note

Decision 61 §4 left voice tear-down to a module. This is that module: it
reads `ctx.note.gate`, begins the release from wherever the envelope
currently is when the gate drops, and sets `ctx.note.finished` when the
release completes, which is what `PolyGraph` reclaims a slot on. Take it out
of a patch and released notes drone -- tested, both ways round.

The gate is read once per block, so a note-off lands at the next block
boundary (11.6ms at 512 frames). That is inherent to block-at-a-time
processing (contract rule 1) and is the granularity every other engine in
this project already has; sample-accurate note timing is an event-list
change to the contract, not something one module can fix.

The envelope's audio-in/audio-out shape, rather than a `PORT_MOD` output
plus a VCA module, is deliberate: decision 56 §5's modulation cables are for
*knobs*, and a gain applied to sound is sound. An envelope with a mod output
is a different module and can sit beside this one when #208 lands.

## 3. Where a knob is a knob, and where it is a new module

The oscillator's waveform is a construction choice (decision 56 §2) because
it selects a 12-band mip table set built by inverse FFT under a lock.
Applying the same test to the two new stepped choices gives two different
answers, and the difference is worth naming:

- **Filter type is a parameter.** All three types share one denominator and
  differ only in the numerator -- the "one structure, one extra output tap"
  property decision 42 chose the SVF for. There is nothing to build.
  `modulatable=False`, though: a modulation cable landing on it would sweep
  the filter through highpass on the way from lowpass to bandpass.
- **Noise colour is a parameter.** Both colours are the same generator with
  or without one 3rd-order filter whose entire state is three float64,
  allocated at activation either way. The price, stated rather than hidden:
  because the colour can change at any block, the module needs SciPy to
  *activate* even when it is set to white. That follows #111's refuse-to-open
  rule, but a white-only noise source on an install without the `[synth]`
  extra is something this design gives up.

Each noise instance owns its own `Generator`, and `new_instance()`
deliberately drops any seed. Sixteen voices from one seed would be one signal
summed sixteen times -- a correlated 16x boost, not noise.

The filter declares `POLY_PER_NOTE` rather than `POLY_EITHER`: its `zi` is
voice history, and its key tracking reads `ctx.note.pitch`, which is `None`
right of Mix. That forbids a master filter on the once-only side today; it is
a one-word change once there is something for key tracking to track there,
and the conservative direction is the one that cannot produce a wrong sound.

## 4. Contract rule 2 is broken in exactly two places, by SciPy

`process()` is supposed to allocate nothing. `scipy.signal.lfilter` returns a
freshly allocated output array and a fresh `zf`, has no `out=`, and there is
no pure-NumPy substitute for an IIR recurrence -- decision 42 is the
measurement (594us a block in a Python loop against 10.9us in `lfilter`'s C).
So the filter, and the noise module when it is pink, each allocate **one
block-sized float64 array per block** and nothing else: 4kB at this app's
512-frame block.

The choice was between hiding that and stating it. It is stated:
`tests/test_synth_graph_voice.py` gives those two modules a budget of exactly
one block, gives the envelope and white noise a budget of zero, and carries a
further test asserting that the budget is actually *used* -- so if a future
SciPy grows an `out=`, the budget fails rather than being carried forever as
a licence. Removing it altogether means a C or Cython inner loop behind
#145's seam, which is the same seam that would replace the whole module.

Everything else is `out=`. Notable: `Generator.random(out=...)` rescaled to
[-1, 1) rather than `uniform(-1, 1)`, which allocates its result.

## 5. The parity test, and what it honestly cannot say

#205's "done when" asks for output identical to the fixed engine when wired
in the equivalent order. Osc -> Filter -> Amp Env through a real
`ModuleGraph`/`PolyGraph`, against a real `synth_engine.SynthVoice` on the
same patch:

- **the first block is bit-identical**, whole note including note-off and
  release;
- later blocks drift, 1.1e-12 at block 1 growing to ~8e-12 by the end of the
  note -- around -220 dB, linear in the note's length and not divergent. The
  cause is float64 association, not the signal path: the two phase
  accumulators add the same terms in a different order.

What it does **not** establish is the more important half, and the test's
docstring says so at length rather than leaving it to be discovered:

- the patch has `lfo.depth = 0`, `filter.env_amount = 0`, `voice.glide = 0`,
  osc2 and noise at zero -- because the graph has no LFO module, no filter
  envelope, no glide, and noise cannot be compared sample-wise at all;
- `voice.volume = 1.0` and `velocity_to_filter = 0`, because those are
  `SynthVoice` voice settings with no module to live on.

And with every modulation source off, **nothing in the patch varies at
control rate**. `SynthVoice` runs a 64-sample sub-block grid and updates
filter coefficients on it; the graph module computes coefficients once per
block. The parity test cannot see that, because at a constant cutoff
`SynthVoice`'s own run-merging collapses its grid to one `lfilter` call per
block -- the identical operation. So a second test asserts the two engines
**disagree** once `filter.env_amount` is turned up, which keeps the first
from being read as a claim the graph reproduces the engine in general.

That control-rate gap is the real remaining difference between the two
engines, and it belongs to #208's modulation layer: block-rate coefficient
updates are 86Hz of modulation bandwidth against the grid's 689Hz, which
decision 42 measured as the difference between a smooth filter sweep and an
audibly stepped one.
