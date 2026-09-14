# 59. The graph module contract, and plugin hosting moved into the design (ticket #202, map #179)

The first ticket of decision 56's engine stream, plus one amendment to
decision 56 the project owner made when the stream opened:

> "i want to build out a system that could take in vst plugins and our own
> moduals and let them work in a node system as the ui intales"

Decision 56 §10 had plugin hosting as informational and explicitly "a later
phase", with the door held open by nothing more specific than "every module
being a node with typed ports". That is no longer good enough. Hosting is
now a stated goal of the system, which changes nothing about *what* gets
built first and quite a lot about *how* the contract is shaped.

## 1. The contract is shaped like a plugin ABI, not like a base class

`audio/graph/contract.py`. Five rules, each of which is also something CLAP
and VST3 both do, and the overlap is the point -- an adapter around a hosted
plugin has to be a `Module` like any other, with no privileges and no
special case anywhere above it.

- **Block-at-a-time `process()`**, N frames in, N frames out. Decision 56 §6.
- **No allocation in the callback.** Everything is allocated in
  `activate(Activation(sample_rate, max_block))` and freed in
  `deactivate()` -- which is the plugin lifecycle verbatim, and for the
  same reason: those are the only two facts a module may size a buffer
  against, and a change to either is a re-activation rather than a message.
- **Ports are declared per instance** (`ports() -> tuple[PortSpec, ...]`),
  not as a class constant. Ours are constant; a plugin's come from the
  plugin. The canvas draws a node's sockets from this, so a hosted plugin
  gets sockets without the canvas ever learning that plugins exist.
- **Typed ports**: `PORT_AUDIO`, `PORT_MOD` (decision 56 §5) and
  `PORT_EVENT`. The third is this decision's addition -- a hosted
  instrument is fed *notes*, not sound, and a note port introduced later
  would be a second contract bolted onto the first.
- **Parameters out of band**: a knob write lands in a preallocated
  `ParamBlock`; `process()` reads one float64 array. Values travel in their
  plain unit (Hz, seconds, 0..1 level) because that is what a patch file
  stores, with `normalize()`/`denormalize()` to the 0..1 that a plugin ABI
  and a knob widget both speak, `log=True` where linear travel is useless,
  and `steps` for a discrete choice -- a flag on the ordinary parameter, not
  a second kind of thing, so there is one automation path.

## 2. `block_delay` is a guarantee, `latency_frames()` is a measurement

Two fields that sound alike and do completely different jobs.
`descriptor().block_delay` is how many whole blocks a module *promises* its
output is behind its input; it is 0 for everything except a delay line, and
it is the single fact #203's cycle rule turns on (decision 56 §4: a feedback
loop is legal only through a module reporting at least 1).
`latency_frames()` is ordinary reported latency for compensation, which a
plugin routinely has and our modules mostly do not.

`Delay` enforces its guarantee by flooring its delay to `max_block` frames
rather than by trusting the knob, so the read window lies entirely behind
the write window and a cycle can be *ordered* rather than solved.

## 3. Sample-accurate automation is deliberately absent

`ParamBlock` is block-rate: one value per parameter per block, not an event
list with a frame offset per change. CLAP delivers the latter and #208's
modulation layer will need it. It is not here because it is #208's problem
and because the extension is additive -- an offset queue drained at block
start, feeding the same array `process()` already reads.

## 4. What #202 shipped

`WavetableOscillator` (per-note) and `Delay` (once-only, `block_delay=1`),
both real, both headlessly tested, no audio device involved anywhere. The
oscillator's DSP is `audio/synth_engine.py`'s, imported rather than copied
-- decision 56 §7 says the graph engine lives *beside* the old one, and
forking the tables would be the first step to a second synth nobody
maintains.

**The no-allocation rule is measured, not asserted.** Writing it in a
docstring and moving on is how it gets broken on the next edit, and the
obvious check does not work: a NumPy temporary is freed the instant the
expression ends, so a before/after snapshot of live memory shows nothing
whether the module allocates or not. The test compares *peak* traced memory
at a 64-frame block against a 2048-frame one -- tracing overhead does not
scale with the block, a leaked temporary does -- and carries a deliberately
sloppy module alongside, so the check is known to be capable of failing.

It found a real one immediately: `np.take(table, idx, out=...)` allocates an
index copy to bounds-check against, 18kB per block at 2048 frames, the
largest allocation in the oscillator. `ndarray.take(..., mode="wrap")` does
not -- and the wrap also removes the modulo on the upper table index, which
is what a cyclic table wanted anyway.

## 5. Which plugin format is still open

Decision 56 §10's conclusion (CLAP, on the new grounds that it is plain C,
`ctypes`-bindable and binary-stable, VST3's licence having ceased to be the
argument when it went MIT with SDK 3.8) is untouched by this decision and
not re-settled by it. Nothing in the contract commits to either format: both
describe a module as ports plus parameters plus an activate/process
lifecycle, which is exactly what is written down here. The choice is a
ticket of its own, and its real content is that **no Python CLAP host
exists** while the mature Python VST3 route (`pedalboard`) is GPL-3.0
because it embeds JUCE -- a licensing problem for an MIT project
(decision 47).
