"""A plain gain stage (#218): the mechanism, where #222 is the default.

Decision 63 §4 settled that stability inside a feedback loop is the
player's problem and that nothing silently clamps their sound -- every
surveyed system agrees, and the engine cannot know a loop's gain anyway.
That position stands. What it left the player without is any way to *act*
on it: a delay's wet/dry mix is unity gain (dry + wet), so patching its
output back into its input adds the whole signal again, and there was no
module on the once-only side of the canvas that could turn that back down
(decision 65 §8). This is that module -- the smallest thing that could be:
one audio in, one audio out, one gain knob, nothing else.

**Bipolar on purpose.** The knob runs -2..2 rather than 0..1: a negative
gain costs nothing extra to compute and, inside a feedback loop, inverting
a repeat is a real timbral choice (comb-filtering the tail into something
brighter or hollower) rather than a mistake a range limit should prevent.
Above 1 is deliberately reachable too -- the same reason a mixing desk's
trim knob does not stop at unity -- because a quiet source patched into a
loop is a legitimate reason to want gain back, not just attenuation.

**A modulatable gain.** `level` is `modulatable=True`, and as of the
follow-up on this ticket it is genuinely *read* that way: a live buffer on
the knob is applied sample by sample rather than being quietly ignored in
favour of the smoothed scalar. That is the same buffer substitution
`noise.py` does for its own `level` and `short_delay.py` for
`feedback`/`mix` (decision 66, #208 stage 2), and it needs no argument of
its own -- a per-sample scale is what a gain stage's arithmetic already is,
so an LFO or envelope on this knob is a tremolo, and inside a feedback loop
it is a decay the player can shape rather than one number. It shipped
inert: the cable compiled, was accepted, and changed nothing audible.

**`POLY_EITHER`.** A gain stage is equally at home per voice (taming one
oscillator before it hits a shared filter) or once-only (the feedback-loop
case this was built for); nothing about the module cares which side of Mix
it ends up on, so the patch decides (decision 61 §2).
"""

from __future__ import annotations

import numpy as np

from notecolor.audio.graph import contract
from notecolor.audio.graph.contract import (
    Module, ModuleDescriptor, ParamSpec, audio_in, audio_out,
)


class Level(Module):
    """One audio in, one audio out, one bipolar gain parameter."""

    def descriptor(self):
        return ModuleDescriptor(
            module_id="level",
            name="Level",
            poly=contract.POLY_EITHER,
            block_delay=0,
            category="utility",
        )

    def ports(self):
        return (audio_in("in", "In"), audio_out("out", "Out"))

    def parameters(self):
        return (
            # Bipolar and reaching past unity in both directions -- see the
            # module docstring for why 1.0 is the middle of the range
            # rather than the top of it.
            ParamSpec("level", "Level", -2.0, 2.0, 1.0),
        )

    def _allocate(self, activation):
        self._in_index = self.port_index("in", contract.DIRECTION_IN)
        self._out_index = self.port_index("out", contract.DIRECTION_OUT)
        self._p_level = 0   # the only parameter; resolved once, not by name

    def process(self, ctx):
        """Multiply into the host's output buffer -- no allocation, and no
        rebind: writing the input array straight out as the output would
        make two nodes share one buffer, which the host's binding does not
        expect (see `passthrough.Passthrough.process()` for the same
        note).

        The multiplier is either the smoothed scalar or this block's
        per-sample buffer, exactly as `noise.py` chooses for its own
        `level` -- `np.multiply(a, b, out=)` takes an array or a float in
        `b` without caring which, so the branch costs one lookup and no
        second copy of the arithmetic."""
        n = ctx.frames
        if n <= 0:
            return
        out = ctx.outputs[self._out_index]
        source = ctx.inputs[self._in_index]
        mod_active = ctx.param_mod_active
        live = mod_active is not None and mod_active[self._p_level]
        level = (ctx.param_buffers[self._p_level][:n] if live
                 else ctx.params[self._p_level])
        np.multiply(source[:n], level, out=out[:n])
