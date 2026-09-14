"""The once-only module the contract is proved against (#202), and the one
module decision 56 §4 gives a special power to: a delay line, the only thing
a feedback cable may legally pass through.

It runs right of the Mix node -- one instance for the whole patch, not one
per note -- and it is what makes `descriptor().block_delay` a real field
rather than a speculative one. #206 builds the loop; this is the module the
loop will be built around, and it is here now because a contract with a
`block_delay` field and nothing that reports 1 is a contract nobody has
tested.

**The guarantee.** Its output for block K never depends on its input for
block K. That is enforced by flooring the delay to `max_block` frames, not
by trusting the knob: the read window then lies entirely behind the write
window, so the graph can order a cycle through this module without the cycle
having to be solved. It is the same trade Bitwig makes -- you get real
feedback, and the price is one visible block of latency (11.61 ms at 512
frames / 44100 Hz) that you placed on the canvas yourself.
"""

from __future__ import annotations

import numpy as np

from notecolor.audio.graph import contract
from notecolor.audio.graph.contract import (
    Module, ModuleDescriptor, ParamSpec, audio_in, audio_out,
)

#: Longest delay the line can be asked for. Fixes the buffer `activate()`
#: allocates, so the knob's top end is a memory decision, not a DSP one --
#: two seconds at 44.1kHz is 353kB, and the module allocates it whether the
#: knob is at 2s or at 20ms.
MAX_DELAY_SECONDS = 2.0


class Delay(Module):
    """A mono delay line with feedback and a dry/wet mix.

    Feedback here is the *internal* kind -- the line's own output folded
    back into its own input, which is what a delay effect is. That is a
    different thing from the patch-level feedback loop decision 56 §4
    legalises, where the cable leaves this module, goes through others and
    comes back. This module enables the second by guaranteeing a block of
    delay; it implements the first because a delay without it is an echo you
    hear once.
    """

    def __init__(self):
        self._write = 0

    # -- scan ---------------------------------------------------------------

    def descriptor(self):
        return ModuleDescriptor(
            module_id="delay.mono",
            name="Delay",
            poly=contract.POLY_ONCE,
            # The whole reason this module can close a loop.
            block_delay=1,
            category="effect",
        )

    def ports(self):
        return (audio_in("in", "In"), audio_out("out", "Out"))

    def parameters(self):
        return (
            ParamSpec("time", "Time", 0.001, MAX_DELAY_SECONDS, 0.25,
                      unit="s", log=True),
            # Capped below 1.0: a delay at unity feedback never decays, and
            # inside a patch-level loop that is a sound that grows without
            # bound until the master clip catches it.
            ParamSpec("feedback", "Feedback", 0.0, 0.95, 0.35),
            ParamSpec("mix", "Mix", 0.0, 1.0, 0.5),
        )

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
            int(round(MAX_DELAY_SECONDS * activation.sample_rate)), self._min_frames)
        # One block of slack past the longest delay, so a read window never
        # overlaps the write window even at maximum time.
        self._size = self._max_frames + activation.max_block
        self._buffer = np.zeros(self._size, dtype=np.float64)
        self._wet = np.zeros(activation.max_block, dtype=np.float64)

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

        delay = int(values[self._p["time"]] * ctx.sample_rate)
        # The guarantee, applied here and not at the knob: however short the
        # user asks for, the read stays a whole block behind the write.
        delay = min(max(delay, self._min_frames), self._max_frames)
        feedback = values[self._p["feedback"]]
        mix = values[self._p["mix"]]

        read = (self._write - delay) % self._size
        self._copy_out(read, n)
        self._copy_in(source, feedback, n)
        self._write = (self._write + n) % self._size

        # out = dry*(1-mix) + wet*mix, in place, in the host's buffer.
        np.multiply(self._wet[:n], mix, out=out[:n])
        np.multiply(source[:n], 1.0 - mix, out=self._wet[:n])
        np.add(out[:n], self._wet[:n], out=out[:n])

    def _copy_out(self, read, n):
        """`self._wet[:n]` <- `n` frames from the ring at `read`, wrapping."""
        first = min(n, self._size - read)
        np.copyto(self._wet[:first], self._buffer[read:read + first])
        if first < n:
            np.copyto(self._wet[first:n], self._buffer[:n - first])

    def _copy_in(self, source, feedback, n):
        """Write `source + feedback * delayed` into the ring at the write
        head. The delayed term is `self._wet`, already read -- which is what
        makes the internal feedback path exactly as long as the delay and
        not one block longer."""
        write = self._write
        first = min(n, self._size - write)
        target = self._buffer[write:write + first]
        np.multiply(self._wet[:first], feedback, out=target)
        np.add(target, source[:first], out=target)
        if first < n:
            rest = n - first
            target = self._buffer[:rest]
            np.multiply(self._wet[first:n], feedback, out=target)
            np.add(target, source[first:n], out=target)
