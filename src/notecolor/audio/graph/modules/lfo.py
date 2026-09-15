"""The LFO (#208, decision 56 §5's settlement §5): the first modulation
source, and the module stage 1 is built to prove -- once this can audibly
wobble a filter's cutoff, per-note and global, the parameter path
(`contract.ParamBlock`'s scalar-or-buffer split), the modulation routing
table (`graph.ModConnection`/`ModRoute`) and the poly-boundary refusal rule
(`poly.PolyGraph.judge_modulation()`) all have something real running
through them.

**Both jacks.** `PORT_MOD` out for a cable that snaps to a knob, and
`PORT_AUDIO` out for a cable that patches into an ordinary input -- the
settlement's deliberate softening of decision 56 §5's "modulation sources
are not audio nodes". Both outputs carry exactly the same signal; the two
sockets exist so the *cable* can still be the thing that says which kind
of connection this is (a mod cable is thin and a different colour, per
decision 56 §5 and #210 §3), which is the legibility reason §5 gave for
the split in the first place. This module does not grow a third socket
kind, and nothing about it should be read as licence to give every module
both an audio and a mod output "for completeness" -- see the decision doc
for the line the settlement draws here.

**The per-note/global switch is baked into which module this is, not a
runtime knob.** The same pattern `oscillator.WavetableOscillator` already
uses for its waveform: which side of the Mix boundary a node may sit on
(`descriptor().poly`) has to be a *fixed* fact by the time the patch
compiles, because a `PolyGraph` builds sixteen copies of the per-note side
and one of the once-only side before a single block runs -- there is no
place a live "per-note vs global" toggle could take effect. So the switch
is `mode`, a constructor argument, exactly the way a waveform choice is:
dragging "LFO (Per Note)" versus "LFO (Global)" out of the drawer replaces
the module (decision 56 §2), and `new_instance()` carries the mode across
to the voice clones the per-note case needs.

**Retrigger.** Decision 56 §5's settlement leaves this to the code: "a
per-note LFO that does not restart on each note is also a real musical
option. If it needs a knob, give it one." It needs one, because the
settlement's own description of per-note mode -- "each held note's LFO
starts with that note, so three held notes shimmer out of phase" -- is
only true if the phase resets at every note-on; a free-running per-note
LFO (reusing a voice slot's phase from whatever the previous note left it
at) is a different, real sound, not a bug in this one. `retrigger`
defaults on, matching the settlement's own description; a global LFO
never restarts regardless of the knob, because the once-only side has no
note-on to restart from (`reset()` is never called on it after the
patch first activates).
"""

from __future__ import annotations

import numpy as np

from notecolor.audio.graph import contract
from notecolor.audio.graph.contract import (
    Module, ModuleDescriptor, ParamSpec, audio_out, mod_out,
)

#: In `synth_engine.lfo_shape()`'s order and convention, so the two do not
#: quietly disagree about what "triangle" means -- this module does not
#: import that function (it is scalar, per-phase; this one has to write a
#: whole block with `out=`), but the shapes are the same shapes.
SHAPES = ("sine", "triangle", "square", "saw")

#: Slow enough to be "a wobble" and fast enough to still be useful as an
#: audio-rate oscillator through the softened audio jack (decision 56 §5's
#: settlement §5) -- 20Hz is where an LFO starts to sound like a pitch
#: rather than a modulation.
RATE_MIN_HZ = 0.02
RATE_MAX_HZ = 20.0

_MODES = ("per_note", "global")


class Lfo(Module):
    """One low-frequency oscillator, with a mod jack and an audio jack.

    `mode` fixes `descriptor().poly` at construction (see the module
    docstring for why this cannot be a runtime parameter): `"per_note"`
    -- the default -- instantiates once per held voice; `"global"` once,
    on the once-only side of Mix.
    """

    def __init__(self, mode="per_note"):
        if mode not in _MODES:
            raise contract.ContractError(f"unknown LFO mode {mode!r}")
        self.mode = mode
        self._phase = 0.0

    # -- scan -----------------------------------------------------------

    def descriptor(self):
        label = "LFO (Per Note)" if self.mode == "per_note" else "LFO (Global)"
        poly = (contract.POLY_PER_NOTE if self.mode == "per_note"
                else contract.POLY_ONCE)
        return ModuleDescriptor(
            module_id=f"lfo.{self.mode}",
            name=label,
            poly=poly,
            category="modulator",
        )

    def ports(self):
        return (mod_out("mod", "Mod"), audio_out("out", "Out"))

    def parameters(self):
        return (
            ParamSpec("rate", "Rate", RATE_MIN_HZ, RATE_MAX_HZ, 2.0,
                      unit="Hz", log=True),
            ParamSpec("shape", "Shape", 0, len(SHAPES) - 1, 0,
                      steps=len(SHAPES), modulatable=False),
            ParamSpec("phase", "Phase", 0.0, 1.0, 0.0, unit="turns"),
            # See the module docstring: this is the knob the settlement
            # left open, not an afterthought.
            ParamSpec("retrigger", "Retrig", 0, 1, 1, steps=2, modulatable=False),
        )

    def new_instance(self):
        """Overridden because `mode` is not a parameter -- the default
        `self.__class__()` would hand every voice a global LFO's poly
        mode, which is not a parameter a voice clone carries; see
        `oscillator.WavetableOscillator.new_instance()` for the same
        reasoning about its waveform."""
        return Lfo(self.mode)

    # -- lifecycle --------------------------------------------------------

    def _allocate(self, activation):
        self._mod_index = self.port_index("mod", contract.DIRECTION_OUT)
        self._audio_index = self.port_index("out", contract.DIRECTION_OUT)
        self._p = {spec.param_id: i for i, spec in enumerate(self.parameters())}
        n = activation.max_block
        self._phases = np.zeros(n, dtype=np.float64)
        self._scratch = np.zeros(n, dtype=np.float64)
        self._bool_scratch = np.zeros(n, dtype=bool)

    def reset(self):
        """Restart the phase, if `retrigger` says to. A no-op on a
        `mode="global"` instance in practice, not by a branch here but
        because nothing ever calls `reset()` on the once-only side again
        after the patch first activates -- see the module docstring."""
        if self.params is None:
            self._phase = 0.0
            return
        if self.params.get("retrigger") >= 0.5:
            self._phase = 0.0

    # -- the audio thread ---------------------------------------------------

    def process(self, ctx):
        n = ctx.frames
        if n <= 0:
            return
        values = ctx.params
        rate = values[self._p["rate"]]
        phase_offset = values[self._p["phase"]]
        shape = int(values[self._p["shape"]])

        dt = rate / ctx.sample_rate
        # Same cumsum-of-a-constant trick `oscillator.py` uses to build an
        # `arange`-shaped ramp with `out=` instead of allocating one.
        phases = self._phases
        phases[:n] = dt
        np.cumsum(phases[:n], out=phases[:n])
        phases[:n] += self._phase - dt
        self._phase = (self._phase + dt * n) % 1.0
        if phase_offset:
            phases[:n] += phase_offset
        np.mod(phases[:n], 1.0, out=phases[:n])

        mod_out_buf = ctx.outputs[self._mod_index]
        self._shape_into(shape, phases, mod_out_buf, n)
        np.copyto(ctx.outputs[self._audio_index][:n], mod_out_buf[:n])

    def _shape_into(self, shape, phases, dest, n):
        """One bipolar waveform, written into `dest[:n]`. Every branch
        uses `self._scratch`/`self._bool_scratch` rather than an
        expression that would allocate its own temporary."""
        scratch = self._scratch
        if shape == 0:  # sine
            np.multiply(phases[:n], 2.0 * np.pi, out=scratch[:n])
            np.sin(scratch[:n], out=dest[:n])
        elif shape == 1:  # triangle
            # 0 at phase 0, +1 at 0.25, -1 at 0.75, matching
            # `synth_engine.lfo_shape()`'s own convention: q = (p+0.75)%1,
            # tri = 4*|q-0.5|-1.
            np.add(phases[:n], 0.75, out=scratch[:n])
            np.mod(scratch[:n], 1.0, out=scratch[:n])
            scratch[:n] -= 0.5
            np.abs(scratch[:n], out=scratch[:n])
            np.multiply(scratch[:n], 4.0, out=dest[:n])
            dest[:n] -= 1.0
        elif shape == 2:  # square
            np.greater_equal(phases[:n], 0.5, out=self._bool_scratch[:n])
            np.multiply(self._bool_scratch[:n], -2.0, out=dest[:n])
            dest[:n] += 1.0
        else:  # saw
            np.multiply(phases[:n], 2.0, out=dest[:n])
            dest[:n] -= 1.0
