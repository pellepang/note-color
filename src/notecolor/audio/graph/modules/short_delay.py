"""The short delay (decision 64): the other half of a question `delay.py`
raised and could not answer on its own.

`modules/delay.py` floors every delay to one whole block -- 11.61 ms at 512
frames / 44100 Hz -- because that floor is the guarantee the graph's cycle
rule is built on (decision 60 §2, decision 63 §2). That is exactly right for
an echo and exactly wrong for the delays that *make* a sound rather than
repeat one: a chorus lives around 15-30 ms, a flanger around 1-10 ms, and a
comb filter well below a millisecond. None of those fit above the floor.

The two behaviours cannot live in one module. A delay shorter than a block
has its output depending on that block's input, which makes
`block_delay = 1` false, and every feedback loop the graph ordered around it
wrong. So this is a **separate module that reports `block_delay = 0` and
therefore cannot close a feedback loop** -- the graph refuses that cable
with the ordinary reason, naming the Delay it wants instead.

The owner chose to have both in the drawer, knowing the cost: two parts that
look alike and behave differently. The honest mitigation is naming, not
cleverness -- "Delay" repeats, "Short Delay" colours -- plus the refusal
sentence, which is the moment the difference actually matters and the only
moment it needs explaining.

## How it runs a delay shorter than a block

`effects.py`'s chunking, ported (#205 says port rather than rewrite): the
block is walked in chunks no longer than the delay itself, so a chunk's
reads can only ever touch samples an *earlier* chunk wrote. That is what
makes read-before-write correct for a feedback delay shorter than one
block, and it is why the same file's `Delay` can do sub-block times while
this project's graph `Delay` deliberately refuses to.

Unlike `effects.py`, the reads here are one contiguous run in the ring (the
read head trails the write head by a fixed distance), so the chunk loop
needs no index arrays at all -- two slice copies, exactly as `delay.py`
does it. `np.arange` per chunk would be an allocation per chunk, which is
the contract's rule 2 broken once per 512 samples instead of once per
block.

## What it does not have

**No damping.** `delay.py` has it because a long tail that never dulls
rings on identically; at 5 ms the tail is gone before dulling could be
heard, and a one-zero average at that length is a comb filter on top of a
comb filter -- a tone change nobody asked the knob for.

**Modulated feedback/mix, and `time` left out (#208 stage 2, decision
67).** `feedback` and `mix` enter this module's arithmetic the same way
they enter `delay.py`'s -- a plain per-sample scale inside `_chunk()` --
so they get the identical buffer substitution. `time` does not, for the
same reason `delay.py`'s does not: this module already reads the ring at
a fixed offset per chunk (recomputed once a block, not once a sample), and
the very thing this module is best positioned to become -- a proper
audio-rate-modulated chorus/flanger -- needs a per-sample interpolated
read this module does not yet have. See `delay.py`'s docstring for the
full reasoning; it applies here without change, and `modulatable=False`
on `time` is the honest reflection of that rather than a cable that
connects and silently does nothing.
"""

from __future__ import annotations

import numpy as np

from notecolor.audio.graph import contract
from notecolor.audio.graph.contract import (
    Module, ModuleDescriptor, ParamSpec, audio_in, audio_out,
)

#: Default longest delay, and therefore the ring size. 50ms covers chorus,
#: flanger and comb with room above; anything longer is `delay.py`'s job and
#: has a floor for a reason.
MAX_SHORT_SECONDS = 0.05

#: Shortest delay the knob reaches: about 4 samples at 44100 Hz. Below that
#: the interpolation this module does not do would start to matter.
MIN_SHORT_SECONDS = 0.0001


class ShortDelay(Module):
    """A sub-block delay line with feedback and a dry/wet mix.

    Everything `Delay` is, minus the one-block floor and therefore minus the
    ability to close a feedback loop. Its internal feedback is its own and
    works normally -- that is what a flanger is -- and is not the same thing
    as a patch-level loop through the graph.
    """

    def __init__(self, max_seconds=MAX_SHORT_SECONDS):
        #: Sizes the ring, so it is a construction choice for the same
        #: reason `Delay.max_seconds` is: a parameter cannot change memory
        #: that was already allocated. Sixteen per-note copies at the
        #: default cost 16 x 50ms x 8 bytes x 44100 = 282kB all told, which
        #: is why this one needs no warning where `Delay`'s 11MB did.
        self.max_seconds = min(max(float(max_seconds), MIN_SHORT_SECONDS * 2), 1.0)
        self._write = 0

    # -- scan ---------------------------------------------------------------

    def descriptor(self):
        return ModuleDescriptor(
            module_id="delay.short",
            name="Short Delay",
            poly=contract.POLY_EITHER,
            # Zero, and that is the whole point: this module makes no
            # promise about being a block behind, so the graph will not let
            # a feedback cable through it. See the module docstring.
            block_delay=0,
            category="effect",
        )

    def ports(self):
        return (audio_in("in", "In"), audio_out("out", "Out"))

    def parameters(self):
        return (
            # `modulatable=False`: see the module docstring's "Modulated
            # feedback/mix, and `time` left out" section.
            ParamSpec("time", "Time", MIN_SHORT_SECONDS, self.max_seconds,
                      min(0.012, self.max_seconds), unit="s", log=True,
                      modulatable=False),
            # Bipolar, unlike `Delay`'s. A negative feedback comb cancels
            # the fundamental instead of reinforcing it, which is half of
            # what a flanger sounds like and is free to offer here.
            ParamSpec("feedback", "Feedback", -0.95, 0.95, 0.0),
            # Defaults to half, because at these lengths the wet signal
            # alone is not the effect -- the interference between wet and
            # dry is.
            ParamSpec("mix", "Mix", 0.0, 1.0, 0.5),
        )

    def new_instance(self):
        """Carries `max_seconds`, which is not a parameter -- the default
        `self.__class__()` would hand a voice a ring the canvas module was
        not built with."""
        return ShortDelay(self.max_seconds)

    # -- lifecycle ----------------------------------------------------------

    def _allocate(self, activation):
        self._in_index = self.port_index("in", contract.DIRECTION_IN)
        self._out_index = self.port_index("out", contract.DIRECTION_OUT)
        self._p = {spec.param_id: i for i, spec in enumerate(self.parameters())}
        self._max_frames = max(
            int(round(self.max_seconds * activation.sample_rate)), 2)
        # One block of slack past the longest delay, so a chunk's write
        # window can never reach the samples its own reads have not taken
        # yet.
        self._size = self._max_frames + activation.max_block + 2
        self._buffer = np.zeros(self._size, dtype=np.float64)
        self._wet = np.zeros(activation.max_block, dtype=np.float64)
        self._dry = np.zeros(activation.max_block, dtype=np.float64)
        # Only touched when `mix` carries a live modulation buffer (#208
        # stage 2) -- `delay.py`'s `_mix_inv` counterpart.
        self._mix_inv = np.zeros(activation.max_block, dtype=np.float64)

    def reset(self):
        self._write = 0
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

        delay = int(values[p["time"]] * ctx.sample_rate)
        delay = min(max(delay, 1), self._max_frames)

        feedback_live = mod_active is not None and mod_active[p["feedback"]]
        mix_live = mod_active is not None and mod_active[p["mix"]]
        feedback_buf = ctx.param_buffers[p["feedback"]] if feedback_live else None
        mix_buf = ctx.param_buffers[p["mix"]] if mix_live else None
        feedback = values[p["feedback"]]
        mix = values[p["mix"]]

        # A chunk no longer than the delay: `effects.py`'s invariant 3, and
        # the only reason a delay shorter than a block can be correct at
        # all.
        chunk = min(delay, n)
        start = 0
        while start < n:
            stop = min(start + chunk, n)
            self._chunk(source, out, start, stop, delay,
                        feedback, feedback_buf, mix, mix_buf)
            start = stop

    def _chunk(self, source, out, start, stop, delay,
               feedback, feedback_buf, mix, mix_buf):
        count = stop - start
        read = (self._write - delay) % self._size
        self._copy_out(read, count)

        # Ring first, output second: the write needs the dry signal and the
        # wet one, and the output step is about to reuse `_dry`.
        fb_buf = feedback_buf[start:stop] if feedback_buf is not None else None
        self._copy_in(source[start:stop], feedback, fb_buf, count)
        self._write = (self._write + count) % self._size

        target = out[start:stop]
        if mix_buf is not None:
            mb = mix_buf[start:stop]
            np.multiply(self._wet[:count], mb, out=target)
            np.subtract(1.0, mb, out=self._mix_inv[:count])
            np.multiply(source[start:stop], self._mix_inv[:count], out=self._dry[:count])
        else:
            np.multiply(self._wet[:count], mix, out=target)
            np.multiply(source[start:stop], 1.0 - mix, out=self._dry[:count])
        np.add(target, self._dry[:count], out=target)

    def _copy_out(self, read, count):
        first = min(count, self._size - read)
        np.copyto(self._wet[:first], self._buffer[read:read + first])
        if first < count:
            np.copyto(self._wet[first:count], self._buffer[:count - first])

    def _copy_in(self, dry, feedback, feedback_buf, count):
        write = self._write
        first = min(count, self._size - write)
        target = self._buffer[write:write + first]
        if feedback_buf is not None:
            np.multiply(self._wet[:first], feedback_buf[:first], out=target)
        else:
            np.multiply(self._wet[:first], feedback, out=target)
        np.add(target, dry[:first], out=target)
        if first < count:
            rest = count - first
            target = self._buffer[:rest]
            if feedback_buf is not None:
                np.multiply(self._wet[first:count], feedback_buf[first:count], out=target)
            else:
                np.multiply(self._wet[first:count], feedback, out=target)
            np.add(target, dry[first:count], out=target)
