"""White and pink noise (#205), per held note.

The DSP is `synth_engine`'s: a uniform generator, and for pink the
3rd-order IIR approximation of a 1/f spectrum from Julius O. Smith's CCRMA
notes (`PINK_B`/`PINK_A`, measured -3.1 dB/octave from 100Hz to 12.8kHz),
run through `lfilter` with a persistent `zi` and made up to white's loudness
by `config.SYNTH_PINK_GAIN`. Same constants, same filter, imported rather
than copied.

**Colour is a stepped parameter, not a construction choice** -- the
opposite call from the oscillator's waveform, and the difference is worth
stating because the two look alike. A waveform selects one of several
12-band mip table sets, built by inverse FFT and cached per (waveform,
sample rate) behind a lock; switching it is a real build, so the
oscillator's waveform replaces the module (decision 56 §2). Colour selects
between two paths that are both already there: the same generator, with or
without one 3rd-order filter whose entire state is three float64 allocated
at activation whether or not it is used. There is nothing to build, so
there is nothing to justify swapping a module out mid-patch for -- and
noise that can be swept white-to-pink from a knob is worth having.

The price is honest and worth naming: because the colour can change at any
block, this module needs SciPy to *activate* even when it is set to white.
That follows #111's rule -- refuse to open rather than open degraded --
rather than contradicting it, but a white-only noise source on an install
without the `[synth]` extra is a thing this design gives up.

**Independent streams per voice.** Each instance owns its own
`numpy.random.Generator`, made at construction, rather than sharing
`synth_engine`'s module-level `_RNG`. Sixteen voices drawing from one
generator would still be uncorrelated (they interleave draws), but sixteen
voices constructed from one *seed* would be sixteen identical streams
summing coherently at Mix -- 16x one noise signal, not noise. That is why
`new_instance()` deliberately drops any seed it was given: the seed exists
for tests, and a test's determinism must not become a voice's correlation.
"""

from __future__ import annotations

import numpy as np

from notecolor.settings import config
from notecolor.audio import synth_engine
from notecolor.audio.graph import contract
from notecolor.audio.graph.contract import (
    Module, ModuleDescriptor, ParamSpec, audio_out,
)

#: Stepped-parameter positions, in `patch_format.NOISE_COLOURS` order so a
#: patch file's `noise.colour` and this module's knob mean the same thing.
COLOURS = ("white", "pink")

WHITE, PINK = 0, 1


class Noise(Module):
    """One noise source, per held note.

    Per-note rather than once-only because noise is a voice's *source*: a
    single global hiss shared by sixteen notes would not be sixteen times
    as loud (it is one signal, summed once at Mix) but it would be one
    signal, so two notes struck together would have identical noise floors
    rising and falling in lockstep with their envelopes -- which is audible
    as a phasing shimmer rather than as texture.
    """

    def __init__(self, seed=None):
        #: Made here, off the audio thread, and never touched again except
        #: to draw from.
        self._rng = np.random.default_rng(seed)
        self._pink_zi = None

    # -- scan ---------------------------------------------------------------

    def descriptor(self):
        return ModuleDescriptor(
            module_id="noise",
            name="Noise",
            poly=contract.POLY_PER_NOTE,
            category="source",
        )

    def ports(self):
        return (audio_out("out", "Out"),)

    def parameters(self):
        return (
            ParamSpec("level", "Level", 0.0, 1.0, 0.5),
            ParamSpec("colour", "Colour", 0, len(COLOURS) - 1, 0,
                      steps=len(COLOURS), modulatable=False),
        )

    def new_instance(self):
        """A fresh instance with a fresh, unseeded generator -- see the
        module docstring on why sixteen voices must not share a seed."""
        return Noise()

    # -- lifecycle ----------------------------------------------------------

    def _allocate(self, activation):
        self._out_index = self.port_index("out", contract.DIRECTION_OUT)
        self._p = {spec.param_id: i for i, spec in enumerate(self.parameters())}
        # Required even when the colour knob is at white; see the module
        # docstring. #111: refuse to open, never open degraded.
        self._lfilter = synth_engine.signal_module().lfilter
        self._pink_zi = np.zeros(len(synth_engine.PINK_A) - 1, dtype=np.float64)
        self._white = np.zeros(activation.max_block, dtype=np.float64)

    def reset(self):
        """Clear the pinking filter's history. Not the generator: a voice
        reusing a slot wants new noise, and reseeding would be the one way
        to make it reuse the old."""
        if self._pink_zi is not None:
            self._pink_zi[:] = 0.0

    # -- the audio thread ---------------------------------------------------

    def process(self, ctx):
        n = ctx.frames
        out = ctx.outputs[self._out_index]
        if n <= 0:
            return
        values = ctx.params
        level = values[self._p["level"]]
        if level <= 0.0:
            # The host's buffers are reused; a module that returns without
            # writing leaks the previous block into the mix.
            out[:n] = 0.0
            return

        # `Generator.random(out=)` fills a buffer we own. `uniform(-1, 1)`
        # would have been the direct translation of `synth_engine._noise()`
        # and allocates its result, which contract rule 2 forbids; the
        # rescale below is the same distribution by a different route.
        white = self._white
        self._rng.random(out=white[:n])
        np.multiply(white[:n], 2.0, out=white[:n])
        np.subtract(white[:n], 1.0, out=white[:n])

        if int(values[self._p["colour"]]) == PINK:
            # The one allocation here, and it is scipy's: `lfilter` has no
            # `out=`. Exactly the departure `filter.py` documents at length.
            pink, zf = self._lfilter(
                synth_engine.PINK_B, synth_engine.PINK_A, white[:n], zi=self._pink_zi)
            np.copyto(self._pink_zi, zf)
            np.multiply(pink, level * config.SYNTH_PINK_GAIN, out=out[:n])
        else:
            np.multiply(white[:n], level, out=out[:n])
