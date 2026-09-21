# 73. Modulated delay time, and the per-sample interpolated read behind it (issue #228)

Decision 67 §2 wired every destination #208's settlement named except one,
and said so in a paragraph rather than a silence: delay `time`, on both
`Delay` and `ShortDelay`, shipped `modulatable=False` because a moving read
offset is not a knob substitution. This ticket is the read path that
paragraph described, and the lifting of the refusal it justified. `time` is
now genuinely modulatable on both modules, which is what a chorus, a
flanger and a vibrato are all made of.

## 1. Why the refusal is lifted now, and what "now" changed

Nothing about decision 67's reasoning was wrong and none of it is retracted
here. Its refusal rested on a fact about the modules -- each computed
`int(values["time"] * sample_rate)` once a block and read one contiguous
window at that offset -- plus a judgement: that a cable which visibly does
not attach, with a sentence saying why, beats one that connects and
crackles. The fact is what changed. Both modules now carry a second read
path, and the refusal is lifted because the thing it was protecting against
(a read that rounds a moving offset to whole frames and steps a whole
sample at a time) no longer exists on the path a cable reaches.

The shape is decision 66's scalar-or-buffer bargain, unchanged and applied
one level up -- not to an arithmetic term this time but to the whole read:

- **No live buffer on `time`** -- the original integer `_copy_out()`, two
  slice copies at one offset, byte-identical to what shipped before this
  ticket. A patch with no cable there pays nothing at all for this
  feature, which is the property that made it possible to add it to the
  modules already on the callback path rather than to a third delay
  module.
- **A live buffer on `time`** -- `FractionalReader`, a per-sample gather
  with linear interpolation between the two ring samples either side of a
  fractional position, which is what makes the pitch a moving delay
  induces sweep smoothly instead of stepping.

**`modulatable=True` that reads nothing is the failure mode this stream has
already had once** -- `filter.py`'s `key_tracking` in decision 67 §2, an
accepted, silent cable, which that decision called a worse failure than a
refusal because nothing on the canvas says so. So the acceptance is
proved rather than declared: `tests/test_synth_graph_delay_time_mod.py`
holds the evidence, and the stage-2 test that asserted the *refusal* was
inverted in place rather than deleted, so the acceptance is now the thing a
regression would break.

## 2. One shared `FractionalReader`, not one per module

`modules/fractional_read.py` is a new file rather than a method on each
module, for two reasons, only one of which is "don't write it twice":

- **The two would drift.** `Delay` and `ShortDelay` are already close
  enough to look alike in the drawer (decision 64's accepted cost), and
  they are deliberately *not* one module. A ring read written twice would
  diverge in exactly the places it matters -- the wrap, the index dtype,
  which tap gets the modulo -- and the second copy would be the one
  nobody re-read.
- **The interpolation kernel is the part most likely to change.** §3
  leaves the door open to a better one; that upgrade should be local to one
  file whose callers hand over a position and get samples back.

What is deliberately *not* shared is the conversion from the `time`
parameter's seconds to frames, nor the clamp that keeps the read behind the
write. Those are the modules' own invariants and they genuinely differ
(`Delay` floors at a whole block plus one, `ShortDelay` at two frames), so
`reader.frames[:n]` is public and each module fills it. The file promises
one thing only: whatever frame counts it is handed are read smoothly.

## 3. Linear interpolation: what it costs, and when to reach past it

#228 named linear as the usual first answer and said to reach past it
(all-pass, cubic) only if its high-frequency loss is *audible at modulation
depth*. Linear is what shipped, and the loss is worth stating precisely
rather than hand-waving, so a future ticket has a number to argue with: a
linear interpolator is a two-tap FIR whose response depends on the
fractional part, from perfectly flat at `frac = 0` to a gentle lowpass at
`frac = 0.5`, where it is about -3.9 dB at Nyquist and under -0.1 dB below
roughly 6 kHz.

Two things make that acceptable here and both are about where the loss
lands:

- **The wet path is blended against a dry path that lost nothing.** Both
  modules have a mix knob and `ShortDelay` defaults to half of each
  (decision 64), so what survives to the output at any sweep position is a
  fraction of a fraction.
- **A chorus/flanger/vibrato is a short, mostly-single-pass delay.** The
  loss does not compound in the case the ticket was opened for.

**Where it would matter is a long feedback chain**, since the loss
compounds once per repeat -- that is the case to listen to first if a
better kernel is ever wanted, and the upgrade is a change to one file.
**Not decided by ear here:** the machine this was built on is muted, and
nothing in this ticket was heard. Every claim above is either arithmetic
(the FIR response) or an array property under test; "is it audible at
depth" remains genuinely open and is the owner's to answer at a speaker.

## 4. The reader owns its own pre-allocated scratch

Contract rule 2 forbids allocating in the callback, and a fractional read
wants seven block-sized temporaries: the read position, its floor, the
fraction, two `intp` index arrays and the two taps, plus a precomputed
`0, 1, 2, ...` ramp that would otherwise be an `np.arange` per chunk. They
live on the reader, filled by `reader.allocate(max_block)` from each
module's own `_allocate()`, which is the only place either module may
allocate at all.

Bundling them is not tidiness. It means each module gains **one** attribute
rather than seven, and it means "did this path allocate per block" has one
place to be wrong rather than two. It is also allocated whether or not a
cable ever lands on `time` -- the same call `activate()` already makes for
a ring sized to `max_seconds` rather than to where the knob happens to be.
The bill is small against what a delay already carries: 7 x 512 x 8 bytes =
28 kB per instance at the default block, against the 706 kB ring one
two-second `Delay` holds.

Pinned by `test_the_interpolated_read_allocates_nothing_per_block`, which
runs the modulated path at two block sizes and asserts the traced peak does
not scale with the block -- the same two-size trick `short_delay.py`'s
original allocation test uses, for the same reason: an allocation per
*chunk* shows up louder than one per block.

## 5. The guarantee survives the move -- and the chunk length that keeps it

This is the part that needed care, and it is two different problems because
the two modules make two different promises.

**`Delay` promises `block_delay = 1`** (decision 63), which is what makes a
cable-built feedback loop legal at all. That promise is about *read
positions*, not about the knob's range, so it has to hold at every
modulation excursion, including an LFO asking for zero. The clamp therefore
moved onto the per-sample, modulated frame count, and its floor is one
frame *higher* than the scalar path's -- `max_block + 1`, not `max_block`
-- because linear interpolation reads the sample above the floored position
too, so the tap that must stay behind the write head is `position + 1`.
Clamping rather than refusing or wrapping is also the musically right
answer: a sweep pushed past the floor flattens out there instead of folding
over.

**`ShortDelay` promises the opposite** (`block_delay = 0`, decision 64) and
buys its correctness from `effects.py`'s invariant 3 instead: the block is
walked in chunks no longer than the delay, so a chunk's reads can only
touch samples an earlier chunk already wrote. Under modulation the delay is
a different number on every sample, so "the delay" is no longer a number
the chunk length can be cut to. **The chunk is now cut to the block's
*minimum* delay, minus one frame for the interpolator's upper tap.**
Invariant 3 is unchanged and still holds -- it holds for the whole chunk
precisely *because* the smallest delay in the block was the one used -- and
the frame count is clamped to at least two so the chunk is at least one
sample and the loop always terminates, including on a bipolar cable driven
past zero.

Both are tested as read positions rather than asserted as prose:
`Delay` driven to a zero time emits nothing of what that same block wrote,
and `ShortDelay` at near-unity feedback returns a separated echo train
instead of running away inside a chunk.

## 6. Cost: the chunk count is the variable, and the cable is not

The honest shape of the cost, before any number:

- On a block where `time` carries a buffer, the per-sample gather runs
  **instead of** the two slice copies `_copy_out()` would have made, not
  in addition to them -- so the interesting figure is a delta between two
  read paths, never the row on its own.
- **`ShortDelay`'s cost is dominated by how short its delay is, modulated
  or not.** At the 12 ms default one chunk covers a 512-frame block; at
  the knob's 0.1 ms floor it takes about 170. So a `time` cable deep
  enough to sweep into that floor buys the floor's chunk count -- which
  the *unmodulated* module has always cost at that knob setting.
  Modulation did not add a cliff; it added a way to drive onto one that
  the knob could already reach.

`scripts/mod_callback_cost.py` grew what is needed to measure exactly that
separation rather than a vague "is the chorus expensive": a **`chorus`
patch** (the existing `worst` patch plus one cable, so the difference
between the two rows is the interpolated read and nothing else), a
`--chorus-depth` flag to sweep into the floor deliberately, a `--set
node:param=value` flag so `--set delay:time=0.0005` measures the same cliff
with no modulation at all, and a p95 column alongside the existing
mean/p50/p99/max.

**No new figures are recorded here, and that is a gap, not a result.**
Decision 67's cost section and decision 70 §7 both concluded that #228 had
**no measured headroom** against decision 55's 70% trigger -- the worst-case
patch already produced driver-reported xruns before this ticket's cost
landed, on the exact `ShortDelay` this ticket's destination sits on. That
reading is not superseded by anything here. What this ticket can say
against it is structural rather than measured: the unmodulated path is
byte-identical, so no existing patch got slower, and the new path's cost
is bounded by what the same module already costs at the same delay length
from the knob. What it cannot say is whether a 16-voice chorus fits the
budget on this machine; the harness to answer that now exists and running
it is the next honest step, on a machine whose ambient load and governor
are the owner's to control (see decision 70 §§5-6 for why this one's were
not).

## 7. One honest seam, and what is left open

- **A knob edit briefly takes the fractional path.** Decision 66's
  smoothing sets `mod_active` for a block or two while a knob's own fade
  runs, so dragging `time` switches to the interpolated read and then back
  to the integer one, which can leave a sub-sample step where the fade
  settles. That step is the same size as the ones a `time` drag already
  made at every integer boundary it crossed, so it is not a new class of
  artefact; making the scalar path fractional too would charge every
  unmodulated delay in every patch for it. Left as it is, named rather
  than hidden.
- **The canvas's `chorus` node is still a `Passthrough`.** #228 is the
  engine side only: what it makes possible is a *patched* chorus -- an LFO
  onto a Short Delay's `time`, which the canvas can already draw -- not a
  chorus node that plays. Turning that node into a real module (or
  deleting it in favour of the patch) belongs to #227/#179, and nothing
  under `gui/` changed here beyond one stale comment in
  `synth_view.py`'s `UTILITY_PARAM_SPECS` that still said the refusal was
  in force.
- **Nothing was verified by ear.** The machine is muted. Every claim is an
  array property under test (`tests/test_synth_graph_delay_time_mod.py`:
  the impulse splitting linearly at a fractional delay, the two paths
  agreeing when the buffer holds the knob's own value, the block-delay
  guarantee at a zero-driven time, no per-block allocation, and a real LFO
  through a compiled graph), which is this stream's established convention
  -- but "does the chorus sound like a chorus" is the one question that
  convention cannot answer.
- **A better interpolation kernel is not built**, on purpose: see §3 for
  the case that would justify it (a long feedback chain) and the file it
  would be local to.

## Index

See `docs/DECISIONS.md`.
