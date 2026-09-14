"""The module that stands in for a canvas node the engine cannot play yet
(#207).

The Synth View's drawer offers more modules than the graph engine has
implementations for: Chorus is a real effect in `audio/effects.py` but has
no graph module, and a patch loaded from an older workspace can name a
module that no longer exists. Two bad answers were available and both were
rejected:

- **Leave the node out of the engine graph.** The cable into it then goes
  nowhere and the cable out of it comes from nowhere, so the chain the user
  drew and the chain that makes sound are different chains and the screen
  is lying. That is precisely what decision 56 set out to stop.
- **Refuse to build the patch.** One unimplemented module and the whole
  canvas goes silent, with the refusal blaming a cable the user has every
  right to draw.

So the node is built, and it is built as this: a wire. Sound goes in and
the same sound comes out, the node keeps its place in the signal path, the
ordering and the loop rules see it exactly as they see any other module,
and unplugging it changes what you hear in the way unplugging a module
should. The only thing missing is the effect itself.

**It is not silent about being a wire.** `gui/patch_bridge.py` names every
node it had to build this way and the Synth View prints that in its status
bar ("chorus passes sound through"), because a module that quietly does
nothing is a bug report waiting to be written. That sentence is the whole
of the user-facing contract; this file just has to be a very good wire.

`POLY_EITHER` on purpose: the thing it is standing in for could be on
either side of Mix, and the host overrides the side anyway
(`ModuleGraph.add(poly=...)`). `block_delay` stays 0 -- a wire is not a
delay, and a loop must not become legal because one of these is in it.
"""

from __future__ import annotations

import numpy as np

from notecolor.audio.graph import contract
from notecolor.audio.graph.contract import (
    Module, ModuleDescriptor, audio_in, audio_out,
)


class Passthrough(Module):
    """One audio in, one audio out, the same samples in both.

    `name` is a constructor argument rather than a constant because the
    descriptor's name is what a refusal sentence prints when the host has
    not supplied a title, and a graph full of modules all called
    "Passthrough" would make those sentences useless.
    """

    def __init__(self, name="Bypass", module_id="util.passthrough"):
        self.name = name
        self.module_id = module_id

    def descriptor(self):
        return ModuleDescriptor(
            module_id=self.module_id,
            name=self.name,
            poly=contract.POLY_EITHER,
            block_delay=0,
            category="utility",
        )

    def ports(self):
        return (audio_in("in", "In"), audio_out("out", "Out"))

    def new_instance(self):
        return Passthrough(self.name, self.module_id)

    def _allocate(self, activation):
        self._in_index = self.port_index("in", contract.DIRECTION_IN)
        self._out_index = self.port_index("out", contract.DIRECTION_OUT)

    def process(self, ctx):
        """A copy, not a rebind. The host binds an unconnected input to one
        shared zero buffer, and binds one module's output buffer directly as
        the next module's input, so handing our input array straight out as
        our output would make two nodes share one buffer and the next write
        to it would corrupt both."""
        frames = ctx.frames
        np.copyto(ctx.outputs[self._out_index][:frames],
                  ctx.inputs[self._in_index][:frames])
