"""The DAHDSR amp envelope (#205) -- and, more importantly than its shape,
**the module that ends a note**.

`poly.PolyGraph.note_off()` clears `ctx.note.gate` and does nothing else.
Nothing reclaims a voice slot until some module sets `ctx.note.finished`,
and decision 61 §4 is explicit that this is deliberate: a voice manager that
decides on its own when a note has stopped sounding is how a synth ends up
clicking on release, and until an envelope is patched a released note
drones, exactly as a modular with no envelope patched does. This module is
the thing that answers. Patch it and notes end; leave it out and they do
not, and the canvas shows you why.

The envelope itself is `synth_engine.DahdsrEnvelope`, imported rather than
copied (decision 56 §7) -- SF2's delay and hold ahead of a conventional
ADSR, resumable by construction because a note-off can arrive mid-block and
an envelope that were a function of "time since onset" could not survive
that. Two small additions were made to it for this module rather than
around it: `block_into()` (the same walk, into a buffer the caller owns)
and `preallocate()` (a ramp buffer, so a ramping stage does not build an
`arange` per block). Both are arithmetic-identical to what was there, which
is what lets #205's parity test compare this module against `SynthVoice`
sample by sample.

**Gate granularity.** The gate is read once per block, so a note-off takes
effect at the next block boundary -- up to 11.6ms at 512 frames. That is
inherent to a block-at-a-time engine (contract rule 1) and is the same
granularity `sound_engine` already gives every other engine; sample-accurate
note timing is an event-list change to the contract (`PORT_EVENT`), not
something this module can fix on its own.

**Why an audio port and not a modulation output.** An amp envelope that
emitted a `PORT_MOD` signal would need something else to multiply by it,
which in this patch would be a VCA module that exists only to be its
partner. Decision 56 §5's modulation cables are for knobs; a gain applied to
sound is sound. When #208 brings a modulation layer, an envelope with a mod
output is a *different* module and can be added beside this one.
"""

from __future__ import annotations

import numpy as np

from notecolor.audio import synth_engine
from notecolor.audio.graph import contract
from notecolor.audio.graph.contract import (
    Module, ModuleDescriptor, ParamSpec, audio_in, audio_out,
)

#: Longest any stage may be set to, matching `patch_format.Envelope`'s own
#: bound so a patch file and a knob agree on the range.
MAX_STAGE_SECONDS = 30.0

#: Shortest a *ramping* stage may be. A log knob cannot reach zero, and for
#: attack, decay and release that is the right answer rather than a
#: limitation: `DahdsrEnvelope` floors each of them at one sample anyway,
#: and a 1ms ramp is what stops the click an instant one makes. Delay and
#: hold are linear precisely because zero is their *off* position.
MIN_RAMP_SECONDS = 0.001


class _Spec:
    """The five fields `DahdsrEnvelope.__init__` reads off a
    `patch_format.Envelope`.

    Built once, in `_allocate()`, and never looked at again: from then on
    the module writes the envelope's already-converted sample counts
    directly. It exists only so this module can construct a `DahdsrEnvelope`
    without importing the patch format, which has nothing to do with a
    graph.
    """

    __slots__ = ("delay", "hold", "attack", "decay", "sustain", "release")

    def __init__(self):
        self.delay = 0.0
        self.hold = 0.0
        self.attack = 0.005
        self.decay = 0.1
        self.sustain = 0.8
        self.release = 0.2


class AmpEnvelope(Module):
    """A DAHDSR envelope applied as a gain to whatever is patched into it.

    Per-note, necessarily: its whole state is one note's progress through
    its own stages, and `ctx.note` is both what it watches and what it
    writes its answer back into.
    """

    def __init__(self):
        self._env = None

    # -- scan ---------------------------------------------------------------

    def descriptor(self):
        return ModuleDescriptor(
            module_id="env.dahdsr.amp",
            name="Amp Env",
            poly=contract.POLY_PER_NOTE,
            category="modulator",
        )

    def ports(self):
        return (audio_in("in", "In"), audio_out("out", "Out"))

    def parameters(self):
        return (
            ParamSpec("delay", "Delay", 0.0, MAX_STAGE_SECONDS, 0.0, unit="s"),
            ParamSpec("attack", "Attack", MIN_RAMP_SECONDS, MAX_STAGE_SECONDS,
                      0.005, unit="s", log=True),
            ParamSpec("hold", "Hold", 0.0, MAX_STAGE_SECONDS, 0.0, unit="s"),
            ParamSpec("decay", "Decay", MIN_RAMP_SECONDS, MAX_STAGE_SECONDS,
                      0.1, unit="s", log=True),
            ParamSpec("sustain", "Sustain", 0.0, 1.0, 0.8),
            ParamSpec("release", "Release", MIN_RAMP_SECONDS, MAX_STAGE_SECONDS,
                      0.2, unit="s", log=True),
            # 0 ignores velocity entirely (an organ); 1.0 makes velocity 0
            # silence. The same curve `synth_engine.SynthVoice` applies, so
            # the two engines can be compared.
            ParamSpec("velocity", "Vel", 0.0, 1.0, 1.0),
        )

    # -- lifecycle ----------------------------------------------------------

    def _allocate(self, activation):
        self._in_index = self.port_index("in", contract.DIRECTION_IN)
        self._out_index = self.port_index("out", contract.DIRECTION_OUT)
        self._p = {spec.param_id: i for i, spec in enumerate(self.parameters())}
        self._env = synth_engine.DahdsrEnvelope(_Spec(), activation.sample_rate)
        self._env.preallocate(activation.max_block)
        self._gain = np.zeros(activation.max_block, dtype=np.float64)

    def reset(self):
        """Back to the top of the envelope, without rebuilding it.

        `poly.Voice.start()` calls this for every module in a slot at
        note-on, and note-on is the one moment that must not allocate --
        which is why `DahdsrEnvelope.restart()` exists rather than a fresh
        construction here.

        The knobs are pushed in *before* the restart and not only after,
        because `restart()` decides whether the note begins in DELAY or in
        ATTACK by reading `delay_samples`. An envelope restarted against
        last block's stage lengths would skip a delay stage it was supposed
        to have.
        """
        if self._env is None or self.activation is None:
            return
        self._configure(self.params.values, self.activation.sample_rate)
        self._env.restart()

    def _configure(self, values, rate):
        """The knobs, in the units `DahdsrEnvelope` actually walks in.

        Six int conversions, which is cheaper than the bookkeeping needed to
        notice they have not changed -- and unlike the filter's
        coefficients there is no transcendental behind them. A stage already
        counting down keeps the length it started with; only `restart()`
        re-reads them, which is what makes a knob turned during a note
        change that note's *future* rather than its past.
        """
        env = self._env
        p = self._p
        env.delay_samples = int(round(values[p["delay"]] * rate))
        env.hold_samples = int(round(values[p["hold"]] * rate))
        env.attack_samples = max(1, int(round(values[p["attack"]] * rate)))
        env.decay_samples = max(1, int(round(values[p["decay"]] * rate)))
        env.release_samples = max(1, int(round(values[p["release"]] * rate)))
        env.sustain = min(max(float(values[p["sustain"]]), 0.0), 1.0)

    # -- the audio thread ---------------------------------------------------

    def process(self, ctx):
        n = ctx.frames
        out = ctx.outputs[self._out_index]
        if n <= 0:
            return
        source = ctx.inputs[self._in_index]
        values = ctx.params
        env = self._env
        self._configure(values, ctx.sample_rate)

        note = ctx.note
        if note is not None and not note.gate:
            # Idempotent inside `DahdsrEnvelope`: the release begins from
            # wherever the envelope is and a second note-off must not
            # restart it, or repeated note-offs would ring a note on
            # forever.
            env.note_off()

        gain = self._gain
        env.block_into(gain[:n])
        np.multiply(source[:n], gain[:n], out=out[:n])

        if note is not None:
            amount = values[self._p["velocity"]]
            scale = 1.0 - amount * (1.0 - note.velocity)
            if scale != 1.0:
                np.multiply(out[:n], scale, out=out[:n])
            if env.finished:
                # The answer decision 61 §4 leaves to a module: this voice
                # has faded, and `PolyGraph` may have its slot back.
                note.finished = True
