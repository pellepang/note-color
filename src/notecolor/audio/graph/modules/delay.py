"""The delay line: the one module decision 56 §4 gives a special power to,
and the only thing a feedback cable may legally pass through (#206).

It runs on **either** side of the Mix node (#205): once only, for a real
echo with a tail that outlives the key; or once per held note, for the short
modulation-ish uses a per-voice delay is for. Which one it is is decided by
the cables, not by this file (decision 61 §2) -- so everything here has to
be correct sixteen times over as readily as once. See "Both sides of Mix"
below for what that actually costs.

**The guarantee.** Its output for block K never depends on its input for
block K. That is enforced *here*, by flooring the delay to `max_block`
frames, and deliberately not in `graph.py`: the read window then lies
entirely behind the write window, so the graph can order a cycle through
this module without the cycle having to be solved. The graph asks one
question -- `descriptor().block_delay >= 1` -- and this module owes it an
answer that is true for every knob position. Keeping the arithmetic on this
side of the line is what lets the minimum shrink later (smaller sub-blocks,
a compiled core behind #145's seam) without a single edit to the graph.

It is the same trade Bitwig makes: you get real feedback, and the price is
one visible block of latency (11.61 ms at 512 frames / 44100 Hz) that you
placed on the canvas yourself.

**Stability is not enforced here** (decision 63 §4). A patch-level loop at a
gain of 1 or more grows without bound and nothing in this module or in the
graph stops it, because a modular that quietly turned the user's sound down
would be lying about the patch they can see. `nonfinite_blocks` counts the
blocks in which the line went NaN or infinite -- a number for a panel to
show, never a correction applied to the signal.

## What was ported from `audio/effects.py`, and what was not

`effects.py`'s `Delay` is the shipping, tested one (#104, decision 40); #205
says port it rather than rewrite it. Taken:

- **Damping.** A one-*zero* averaging filter in the feedback path, blended
  0..1, Karplus-Strong's rather than the one-pole the research named -- a
  one-pole is a per-sample recursion with no vectorized form, a one-zero is
  two shifted arrays and one carried sample. It is what makes a long tail
  decay into the background instead of ringing on identically, and inside a
  patch-level loop it is the only tone control the loop has until #205's
  filter lands. Off by default, as it is there.
- **Block-partition transparency**, as an acceptance test rather than as
  code. `effects.py` earns it by chunking; this module gets it for free from
  the one-block floor (any sub-block is shorter than the delay, so its reads
  can only touch samples an earlier sub-block wrote), and the test asserting
  it is worth having for the same reason it is worth having there: it is
  exact, not approximate, so any drift fails loudly.

**Modulated feedback/damping/mix, and why `time` is not among them (#208
stage 2, decision 67).** `feedback`, `damping` and `mix` each enter the
recurrence as a plain per-sample scale -- never as a read offset -- so
giving them a live modulation buffer is the same `out=` array substitution
`noise.py`'s `level` gets, applied inside `_damp()` and `_copy_in()`. A
slow LFO breathing the feedback or the wet mix is an ordinary, safe patch.

**Modulated `time`, and the second read path it needed (#228, decision
73).** `time` was the one destination decision 67 left out, and the reason
was real: it is not a per-sample *scale* like the other three, it is a
per-sample *read offset*, and this module computed it once a block
(`delay = int(values[...] * sample_rate)`) and read one contiguous window
at that fixed offset. Rounding a moving offset to whole frames crackles on
every integer jump, so rather than ship that, #208 stage 2 declared
`modulatable=False` and named the work.

That work is now done, and `time` is genuinely modulatable on both
modules. There are two read paths, chosen per block:

- **Nothing modulating `time`** -- the original `_copy_out()`, two slice
  copies at one integer offset, byte-identical to what it always did. A
  patch with no cable on `time` pays exactly nothing for this feature,
  which is the same scalar-or-buffer bargain decision 66 struck
  everywhere else.
- **A live buffer on `time`** -- `fractional_read.FractionalReader`, a
  per-sample gather with linear interpolation between the two ring
  samples either side of the fractional position. That is what makes the
  pitch a moving delay induces sweep smoothly instead of stepping, and it
  is what a chorus, a flanger and a vibrato are all built from.

**The guarantee survives the move**, which was the part that needed care.
The floor is applied per sample, to the modulated frame count, and it is
one frame *higher* than the scalar path's (`max_block + 1` rather than
`max_block`): linear interpolation reads the sample above the floored
position too, so the tap that must stay behind the write head is
`position + 1`, not `position`. With that, every read this block -- both
taps, every sample -- still lands strictly behind the write window, so
`block_delay = 1` remains true at every knob position *and every
modulation excursion*, and a feedback loop the graph ordered around this
module stays correctly ordered while an LFO sweeps its time.

One honest seam: a knob edit's own fade also sets `mod_active` for a block
or two (decision 66's smoothing), so a `time` drag briefly takes the
fractional path and then returns to the integer one, which can leave a
sub-sample step at the moment the fade settles. That step is the same size
as the ones a `time` drag already made on every integer boundary it
crossed, so it is not a new class of artefact, and making the scalar path
fractional too would charge every unmodulated delay in every patch for it.

Deliberately **not** taken:

- **Sub-delay chunking.** `effects.py` splits a block into chunks no longer
  than the delay so that a delay *shorter* than one block still works. That
  is precisely the thing this module may not do: a delay shorter than a
  block has its output depending on this block's input, which makes
  `block_delay=1` false and every feedback loop the graph ordered around it
  wrong. The two behaviours cannot coexist in one instance. If short
  per-note delays are wanted later they need a second module that reports
  `block_delay=0` and therefore cannot close a loop -- a module choice, like
  the oscillator's waveform, not a knob. Flagged for the owner; not invented
  here.
- **float32 and the `Effect` protocol.** The graph is float64 and speaks
  `Module`; converting would be a second contract bolted onto one module.

## Both sides of Mix

`POLY_EITHER` costs memory, and the arithmetic is worth stating rather than
discovering. The ring is sized for `max_seconds`, not for where the knob
happens to be, because the knob moves and `activate()` is the only place
this module is allowed to allocate. At the default two seconds that is
44100 x 2 x 8 bytes = 706 kB, and a per-note delay is *sixteen* of those --
11 MB, allocated up front. That is affordable on the desktop the Synth View
targets (decision 55) and is not obviously affordable on a Pi, so
`max_seconds` is a construction choice: a delay placed for per-note
modulation can be built with a fraction of a second and cost 1/16th as
much. `new_instance()` carries it, which is the whole reason that method is
overridden.

Everything else the per-note case needs was already true and is now tested:
`reset()` zeroes the ring, so a reused voice slot cannot inherit the
previous note's echo; nothing in `process()` reads `ctx.note`, so a voice's
copy behaves identically to the once-only one; and the buffers are sized
from `Activation`, which every clone gets its own of.
"""

from __future__ import annotations

import math

import numpy as np

from notecolor.audio.graph import contract
from notecolor.audio.graph.contract import (
    Module, ModuleDescriptor, ParamSpec, audio_in, audio_out,
)
from notecolor.audio.graph.modules.fractional_read import FractionalReader

#: Default longest delay the line can be asked for. Fixes the buffer
#: `activate()` allocates, so the knob's top end is a memory decision, not a
#: DSP one -- and the module allocates it whether the knob is at 2s or at
#: 20ms. Overridable per instance; see "Both sides of Mix" above.
MAX_DELAY_SECONDS = 2.0


class Delay(Module):
    """A mono delay line with feedback, damping and a dry/wet mix.

    Feedback here is the *internal* kind -- the line's own output folded
    back into its own input, which is what a delay effect is. That is a
    different thing from the patch-level feedback loop decision 56 §4
    legalises, where the cable leaves this module, goes through others and
    comes back. This module enables the second by guaranteeing a block of
    delay; it implements the first because a delay without it is an echo you
    hear once.
    """

    def __init__(self, max_seconds=MAX_DELAY_SECONDS):
        #: The longest the time knob may be turned, and therefore the size of
        #: the ring. A construction choice rather than a parameter because a
        #: parameter cannot change how much memory was already allocated.
        # Floored just above the time knob's own minimum, so the parameter
        # range below cannot collapse to a point.
        self.max_seconds = max(float(max_seconds), 0.002)
        self._write = 0
        self._damp_prev = 0.0
        #: The per-sample interpolated read, used only on blocks where a
        #: live buffer is landing on `time` (#228). Allocated in
        #: `_allocate()` like everything else; inert until then.
        self._reader = FractionalReader()
        #: Blocks in which the delay line held a NaN or an infinity. Read off
        #: the audio thread, by a panel that wants to say so. Never acted on
        #: -- see decision 63 §4.
        self.nonfinite_blocks = 0

    # -- scan ---------------------------------------------------------------

    def descriptor(self):
        return ModuleDescriptor(
            module_id="delay.mono",
            name="Delay",
            # Either side (#205). Once-only for an echo whose tail outlives
            # the key, per-note for a delay inside a voice -- and the cables
            # decide which, not this field.
            poly=contract.POLY_EITHER,
            # The whole reason this module can close a loop.
            block_delay=1,
            category="effect",
        )

    def ports(self):
        return (audio_in("in", "In"), audio_out("out", "Out"))

    def parameters(self):
        return (
            # Modulatable since #228, and the buffer really is read: a
            # live one switches this module onto its per-sample
            # interpolated read path. See the module docstring's
            # "Modulated `time`" section for what that costs and what it
            # keeps true.
            ParamSpec("time", "Time", 0.001, self.max_seconds, min(0.25, self.max_seconds),
                      unit="s", log=True),
            # Capped below 1.0: a delay at unity *internal* feedback never
            # decays. That cap is about this module's own recursion and is
            # not a stability policy for the patch -- the loop a cable makes
            # around several modules has no such cap and is not given one
            # (decision 63 §4).
            ParamSpec("feedback", "Feedback", 0.0, 0.95, 0.35),
            # High-end rolloff on each pass round the internal loop. Off by
            # default, exactly as in `effects.py`: #104 measured nothing
            # about damping, so the shipped default is the untouched one.
            ParamSpec("damping", "Damping", 0.0, 1.0, 0.0),
            ParamSpec("mix", "Mix", 0.0, 1.0, 0.5),
        )

    def new_instance(self):
        """Overridden because `max_seconds` is not a parameter: the default
        `self.__class__()` would hand all sixteen voices a two-second ring
        when the one on the canvas was built for fifty milliseconds."""
        return Delay(self.max_seconds)

    def latency_frames(self):
        """Zero: the dry path is not delayed, so there is nothing for a host
        to compensate. `block_delay` is the field that is non-zero here, and
        it means something else entirely -- see the module docstring."""
        return 0

    # -- lifecycle ----------------------------------------------------------

    def _allocate(self, activation):
        self._in_index = self.port_index("in", contract.DIRECTION_IN)
        self._out_index = self.port_index("out", contract.DIRECTION_OUT)
        self._p = {spec.param_id: i for i, spec in enumerate(self.parameters())}
        self._max_block = activation.max_block
        self._min_frames = activation.max_block
        self._max_frames = max(
            int(round(self.max_seconds * activation.sample_rate)), self._min_frames)
        # The interpolated path's floor is one frame higher than the
        # scalar path's, because its upper tap reads one sample nearer the
        # write head. See "The guarantee survives the move" in the module
        # docstring.
        self._min_mod_frames = float(self._min_frames + 1)
        self._max_mod_frames = float(max(self._max_frames, self._min_frames + 1))
        # One block of slack past the longest delay, so a read window never
        # overlaps the write window even at maximum time.
        self._size = self._max_frames + activation.max_block
        self._buffer = np.zeros(self._size, dtype=np.float64)
        self._wet = np.zeros(activation.max_block, dtype=np.float64)
        # Damping's scratch. Two buffers rather than one because the damped
        # signal is what goes back into the ring while the *undamped* signal
        # is what leaves as the wet output -- damping is in the feedback
        # path, not on the output, which is what makes repeat N darker than
        # repeat N-1 instead of darkening everything once.
        self._prev = np.zeros(activation.max_block, dtype=np.float64)
        self._damped = np.zeros(activation.max_block, dtype=np.float64)
        # Only touched when `damping` or `mix` carries a live modulation
        # buffer (#208 stage 2): the elementwise counterparts of the
        # scalar arithmetic `_damp()` and `process()` otherwise do with
        # `out=` already, one array each for the same reason `filter.py`
        # keeps separate cutoff/resonance scratch -- reused across blocks,
        # never reallocated.
        self._damp_half = np.zeros(activation.max_block, dtype=np.float64)
        self._damp_invhalf = np.zeros(activation.max_block, dtype=np.float64)
        self._mix_inv = np.zeros(activation.max_block, dtype=np.float64)
        # The interpolated read's scratch (#228). Allocated whether or not
        # a cable ever lands on `time`, for the same reason the ring is
        # sized for `max_seconds` rather than for where the knob is:
        # `activate()` is the only place this module may allocate.
        self._reader.allocate(activation.max_block)

    def reset(self):
        """Silence the line without reallocating.

        Called between notes by `poly.Voice.start()`, which is what keeps a
        reused voice slot from playing the previous note's echo underneath
        the new one -- the per-note case's one genuinely new requirement,
        and the reason this clears the whole ring rather than just moving
        the write head.
        """
        self._write = 0
        self._damp_prev = 0.0
        self.nonfinite_blocks = 0
        if self.activation is not None:
            self._buffer[:] = 0.0

    # -- the audio thread ---------------------------------------------------

    def process(self, ctx):
        n = ctx.frames
        if n <= 0:
            return
        source = ctx.inputs[self._in_index]
        out = ctx.outputs[self._out_index]
        values = ctx.params
        p = self._p
        mod_active = ctx.param_mod_active

        time_live = mod_active is not None and mod_active[p["time"]]
        time_buf = ctx.param_buffers[p["time"]] if time_live else None
        if time_buf is None:
            delay = int(values[p["time"]] * ctx.sample_rate)
            # The guarantee, applied here and not at the knob: however short
            # the user asks for, the read stays a whole block behind the
            # write.
            delay = min(max(delay, self._min_frames), self._max_frames)

        feedback_live = mod_active is not None and mod_active[p["feedback"]]
        damping_live = mod_active is not None and mod_active[p["damping"]]
        mix_live = mod_active is not None and mod_active[p["mix"]]
        feedback_buf = ctx.param_buffers[p["feedback"]] if feedback_live else None
        damping_buf = ctx.param_buffers[p["damping"]] if damping_live else None
        mix_buf = ctx.param_buffers[p["mix"]] if mix_live else None
        feedback = values[p["feedback"]]
        damping = values[p["damping"]]
        mix = values[p["mix"]]

        if time_buf is None:
            read = (self._write - delay) % self._size
            self._copy_out(read, n)
        else:
            self._read_modulated(time_buf, n, ctx.sample_rate)
        tail = self._damp(damping, damping_buf, n)
        self._copy_in(source, feedback, feedback_buf, tail, n)
        self._write = (self._write + n) % self._size

        # Whether the line went non-finite this block. A sum rather than a
        # sampled element, because a NaN sits at a *fixed* offset in the ring
        # and so lands at the same position in every read window -- checking
        # one element would miss it forever unless it happened to land
        # there. NaN and either infinity propagate through the sum, so one
        # reduction over the block answers it, and it is a reduction NumPy
        # does in C with no temporary. Counted, never corrected: decision
        # 63 §4.
        if not math.isfinite(self._wet[:n].sum()):
            self.nonfinite_blocks += 1

        # out = dry*(1-mix) + wet*mix, in place, in the host's buffer.
        if mix_buf is not None:
            np.multiply(self._wet[:n], mix_buf[:n], out=out[:n])
            np.subtract(1.0, mix_buf[:n], out=self._mix_inv[:n])
            np.multiply(source[:n], self._mix_inv[:n], out=self._wet[:n])
        else:
            np.multiply(self._wet[:n], mix, out=out[:n])
            np.multiply(source[:n], 1.0 - mix, out=self._wet[:n])
        np.add(out[:n], self._wet[:n], out=out[:n])

    def _damp(self, damping, damping_buf, n):
        """The signal that goes back into the ring: `self._wet`, optionally
        softened by a one-zero average of it with its own previous sample.

        Returns the array to feed back, so the caller has no branch and
        neither array is copied when damping is off. One carried sample of
        state (`_damp_prev`), which is what keeps this transparent to how
        the host happens to split its blocks. `damping_buf`, when not
        `None`, is a live per-sample modulation buffer (#208 stage 2) and
        takes the same shape with `self._damp_half`/`_damp_invhalf` standing
        in for the scalar `half`/`1.0 - half`.
        """
        if damping_buf is None:
            if damping <= 0.0:
                return self._wet
            half = damping * 0.5
            np.copyto(self._prev[1:n], self._wet[:n - 1])
            self._prev[0] = self._damp_prev
            self._damp_prev = self._wet[n - 1]
            np.multiply(self._wet[:n], 1.0 - half, out=self._damped[:n])
            np.multiply(self._prev[:n], half, out=self._prev[:n])
            np.add(self._damped[:n], self._prev[:n], out=self._damped[:n])
            return self._damped

        if float(np.max(damping_buf[:n])) <= 0.0:
            return self._wet
        half = self._damp_half
        inv_half = self._damp_invhalf
        np.multiply(damping_buf[:n], 0.5, out=half[:n])
        np.subtract(1.0, half[:n], out=inv_half[:n])
        np.copyto(self._prev[1:n], self._wet[:n - 1])
        self._prev[0] = self._damp_prev
        self._damp_prev = self._wet[n - 1]
        np.multiply(self._wet[:n], inv_half[:n], out=self._damped[:n])
        np.multiply(self._prev[:n], half[:n], out=self._prev[:n])
        np.add(self._damped[:n], self._prev[:n], out=self._damped[:n])
        return self._damped

    def _read_modulated(self, time_buf, n, sample_rate):
        """`self._wet[:n]` <- the ring read at a delay that moves every
        sample (#228), interpolated between whole positions.

        The clamp is the whole safety story, and it is applied to the
        *modulated* frame count rather than to the knob: whatever an LFO
        asks for, no sample of this block reads nearer the write head than
        one block plus one frame, which is what keeps `block_delay = 1`
        true while the time sweeps. Clamping (rather than refusing or
        wrapping) is also the musically right answer -- a chorus swept past
        the floor flattens out at the floor instead of folding over.
        """
        frames = self._reader.frames
        np.multiply(time_buf[:n], sample_rate, out=frames[:n])
        np.clip(frames[:n], self._min_mod_frames, self._max_mod_frames,
                out=frames[:n])
        self._reader.read_into(self._buffer, self._size, self._write,
                               0, n, self._wet)

    def _copy_out(self, read, n):
        """`self._wet[:n]` <- `n` frames from the ring at `read`, wrapping."""
        first = min(n, self._size - read)
        np.copyto(self._wet[:first], self._buffer[read:read + first])
        if first < n:
            np.copyto(self._wet[first:n], self._buffer[:n - first])

    def _copy_in(self, source, feedback, feedback_buf, tail, n):
        """Write `source + feedback * tail` into the ring at the write head,
        where `tail` is the delayed signal already read (damped or not) --
        which is what makes the internal feedback path exactly as long as the
        delay and not one block longer. `feedback_buf`, when not `None`, is
        a live per-sample modulation buffer (#208 stage 2), sliced the same
        way `tail` already is for the ring's wraparound."""
        write = self._write
        first = min(n, self._size - write)
        target = self._buffer[write:write + first]
        if feedback_buf is not None:
            np.multiply(tail[:first], feedback_buf[:first], out=target)
        else:
            np.multiply(tail[:first], feedback, out=target)
        np.add(target, source[:first], out=target)
        if first < n:
            rest = n - first
            target = self._buffer[:rest]
            if feedback_buf is not None:
                np.multiply(tail[first:n], feedback_buf[first:n], out=target)
            else:
                np.multiply(tail[first:n], feedback, out=target)
            np.add(target, source[first:n], out=target)
