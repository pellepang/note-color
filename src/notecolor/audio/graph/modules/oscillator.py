"""The per-note module the contract is proved against (#202): a mip-mapped
wavetable oscillator, one instance per held note.

The DSP is not new -- the tables, the band selection and the pulse-from-saw
trick are `audio/synth_engine.py`'s, unchanged and imported rather than
copied (decision 56 §7: the graph engine lives *beside* the old one, it does
not fork it). What is new is the shape: where `SynthVoice` reads its
frequency off a `Patch` dataclass and renders a whole fixed signal path,
this reads one float64 array and writes one buffer, and knows nothing about
what is patched into it.

Every array it touches is allocated in `_allocate()` and every NumPy call in
`process()` passes `out=`. That is fussier than the rest of this codebase
and it is the point: the contract's no-allocation rule is worth nothing if
the first module written against it allocates six temporaries a block. A
module that reads like this is also one an eventual C inner loop can replace
line for line.

`level` and `fine` are two of the destinations #208 (decision 56 §5's
settlement §2) names directly as needing per-sample precision: `process()`
reads `ctx.param_buffers` instead of the scalar `ctx.params` for either one
while `ctx.param_mod_active` says it is live this block, and falls back to
the ordinary scalar path otherwise -- the same branch shape `filter.py`
uses for `cutoff`/`resonance`. `octave` and `semitones` cannot receive a
mod cable at all (`modulatable=False`): a waveform pitched by whole
semitones mid-sweep is a different instrument, not a bend.

**`pulse_width` (#208 stage 2, decision 67).** A PWM square -- an LFO
slowly walking the width knob -- is a real, common patch, and unlike
`fine` it needs no chunked coefficient recompute to get it: `pulse_width`
only ever enters `process()` inside vectorized array arithmetic already
(the phase-shift subtraction and the `2*width - 1` DC correction the
pulse-from-saw trick needs), so going from scalar to per-sample buffer is
the same `out=` substitution `level` already makes, not a new control-rate
scheme. It has no effect on the other three waveforms, the same as it
always did -- the knob exists on every instance because it is a parameter,
not a per-waveform capability.
"""

from __future__ import annotations

import numpy as np

from notecolor.audio import synth_engine
from notecolor.audio.graph import contract
from notecolor.audio.graph.contract import (
    Module, ModuleDescriptor, ParamSpec, audio_out,
)

#: The waveforms this module offers. A waveform is a *construction* choice,
#: not a parameter: it decides which table set the instance reads, and a
#: table set is looked up through a lock. Switching one on the canvas
#: replaces the module, which is also what dragging a different oscillator
#: out of the drawer means (decision 56 §2).
WAVEFORMS = ("saw", "square", "triangle", "sine")


class WavetableOscillator(Module):
    """One band-limited oscillator. Per-note: its frequency comes from the
    note the host put in `ctx.note`, so the same module in the same patch is
    sixteen different pitches at once."""

    def __init__(self, waveform="saw"):
        if waveform not in WAVEFORMS:
            raise contract.ContractError(f"unknown waveform {waveform!r}")
        self.waveform = waveform
        self._phase = 0.0

    # -- scan ---------------------------------------------------------------

    def descriptor(self):
        return ModuleDescriptor(
            module_id=f"osc.wavetable.{self.waveform}",
            name=f"Osc ({self.waveform})",
            poly=contract.POLY_PER_NOTE,
            category="source",
        )

    def ports(self):
        return (audio_out("out", "Out"),)

    def parameters(self):
        return (
            ParamSpec("level", "Level", 0.0, 1.0, 0.8),
            ParamSpec("octave", "Octave", -4, 4, 0, steps=9, modulatable=False),
            ParamSpec("semitones", "Semis", -12, 12, 0, steps=25, modulatable=False),
            ParamSpec("fine", "Fine", -100.0, 100.0, 0.0, unit="cents"),
            ParamSpec("pulse_width", "Width", 0.05, 0.95, 0.5),
        )

    def new_instance(self):
        """Overridden because the waveform is not a parameter: the default
        `self.__class__()` would hand voice 2 a saw when voice 1 is a
        square."""
        return WavetableOscillator(self.waveform)

    # -- lifecycle ----------------------------------------------------------

    def _allocate(self, activation):
        self._out_index = self.port_index("out", contract.DIRECTION_OUT)
        self._p = {spec.param_id: i for i, spec in enumerate(self.parameters())}
        self._tables = synth_engine.tables_for(self.waveform, activation.sample_rate)
        self._is_pulse = self.waveform == "square"

        n = activation.max_block
        self._phases = np.zeros(n, dtype=np.float64)
        self._shifted = np.zeros(n, dtype=np.float64)
        self._pos = np.zeros(n, dtype=np.float64)
        self._frac = np.zeros(n, dtype=np.float64)
        self._i0 = np.zeros(n, dtype=np.int64)
        self._i1 = np.zeros(n, dtype=np.int64)
        self._a = np.zeros(n, dtype=np.float64)
        self._b = np.zeros(n, dtype=np.float64)
        self._scratch = np.zeros(n, dtype=np.float64)
        # Only touched when `fine` carries a live modulation buffer (#208):
        # a per-sample frequency multiplier, so pitch modulation is a real
        # sweep rather than one update a block.
        self._dt = np.zeros(n, dtype=np.float64)
        self._exponent = np.zeros(n, dtype=np.float64)

    def reset(self):
        self._phase = 0.0

    # -- the audio thread ---------------------------------------------------

    def process(self, ctx):
        n = ctx.frames
        out = ctx.outputs[self._out_index]
        if n <= 0:
            return
        values = ctx.params
        p = self._p
        mod_active = ctx.param_mod_active

        level_live = mod_active is not None and mod_active[p["level"]]
        level_buf = ctx.param_buffers[p["level"]] if level_live else None
        if level_live:
            # `np.max` rather than `level_buf[:n] > 0.0`: a comparison
            # without `out=` allocates the boolean array it returns, and
            # this is only checking for "silent the whole block".
            if float(np.max(level_buf[:n])) <= 0.0:
                out[:n] = 0.0
                return
        else:
            level = values[p["level"]]
            if level <= 0.0:
                out[:n] = 0.0
                return

        base_frequency = ctx.note.frequency if ctx.note is not None else 440.0
        octave = values[p["octave"]]
        semitones = values[p["semitones"]]
        fine_live = mod_active is not None and mod_active[p["fine"]]

        if not fine_live:
            frequency = base_frequency * 2.0 ** (
                octave + semitones / 12.0 + values[p["fine"]] / 1200.0)
            dt = frequency / ctx.sample_rate
            # phases[i] = (phase + i*dt) mod 1, without building an arange:
            # cumsum of a constant is an arange, and `out=` keeps it in the
            # buffer allocated at activation.
            self._phases[:n] = dt
            np.cumsum(self._phases[:n], out=self._phases[:n])
            self._phases[:n] += self._phase - dt
            next_phase = (self._phase + dt * n) % 1.0
        else:
            # A live pitch-modulation buffer on `fine` (#208, decision 56
            # §5's settlement §2, named directly as one of the
            # destinations this stage has to prove): a per-sample
            # frequency, still without allocating -- `octave`/`semitones`
            # cannot carry a mod cable at all (`modulatable=False`), so
            # only `fine` needs to vary within the block.
            fine_buf = ctx.param_buffers[p["fine"]]
            exponent = self._exponent
            np.multiply(fine_buf[:n], 1.0 / 1200.0, out=exponent[:n])
            exponent[:n] += octave + semitones / 12.0
            dt_arr = self._dt
            np.power(2.0, exponent[:n], out=dt_arr[:n])
            np.multiply(dt_arr[:n], base_frequency / ctx.sample_rate, out=dt_arr[:n])
            frequency = base_frequency * 2.0 ** (
                octave + semitones / 12.0 + float(fine_buf[n - 1]) / 1200.0)
            np.cumsum(dt_arr[:n], out=self._phases[:n])
            total = float(self._phases[n - 1])
            self._phases[:n] += self._phase - float(dt_arr[0])
            next_phase = (self._phase + total) % 1.0

        np.mod(self._phases[:n], 1.0, out=self._phases[:n])
        self._phase = next_phase

        table = self._tables[synth_engine.mip_level_for(frequency)]
        if self._is_pulse:
            # pulse(p, d) = saw(p - d) - saw(p) + (2d - 1), read from the saw
            # table -- `synth_engine.pulse_from_saw()` in place.
            width_live = mod_active is not None and mod_active[p["pulse_width"]]
            if width_live:
                width_buf = ctx.param_buffers[p["pulse_width"]]
                np.subtract(self._phases[:n], width_buf[:n], out=self._shifted[:n])
            else:
                width = values[p["pulse_width"]]
                np.subtract(self._phases[:n], width, out=self._shifted[:n])
            np.mod(self._shifted[:n], 1.0, out=self._shifted[:n])
            self._read_into(table, self._shifted, out, n)
            self._read_into(table, self._phases, self._scratch, n)
            np.subtract(out[:n], self._scratch[:n], out=out[:n])
            if width_live:
                # DC correction 2*width - 1, per sample: reuses `_scratch`,
                # already spent above and free until the level scaling below.
                np.multiply(width_buf[:n], 2.0, out=self._scratch[:n])
                self._scratch[:n] -= 1.0
                np.add(out[:n], self._scratch[:n], out=out[:n])
            else:
                out[:n] += 2.0 * width - 1.0
        else:
            self._read_into(table, self._phases, out, n)

        if level_live:
            np.multiply(out[:n], level_buf[:n], out=out[:n])
        else:
            np.multiply(out[:n], level, out=out[:n])

    def _read_into(self, table, phases, dest, n):
        """Linear interpolation of one table band at `phases`, written into
        `dest[:n]`. `np.take(..., out=)` rather than `table[i0]`, because
        fancy indexing allocates the result."""
        size = table.shape[0]
        np.multiply(phases[:n], size, out=self._pos[:n])
        np.floor(self._pos[:n], out=self._frac[:n])
        self._i0[:n] = self._frac[:n]
        np.subtract(self._pos[:n], self._frac[:n], out=self._frac[:n])
        np.add(self._i0[:n], 1, out=self._i1[:n])
        # `ndarray.take(..., mode="wrap")`, not `np.take` and not `table[i]`.
        # The free function's default mode allocates an index copy to
        # bounds-check against (measured: 18kB a block at 2048 frames, the
        # single largest allocation in this module before it was found), and
        # "wrap" is also what makes the `% size` on the upper index
        # unnecessary -- the gather wraps the last sample back to the first
        # itself, which is what a cyclic table wants.
        table.take(self._i0[:n], out=self._a[:n], mode="wrap")
        table.take(self._i1[:n], out=self._b[:n], mode="wrap")
        np.subtract(self._b[:n], self._a[:n], out=self._b[:n])
        np.multiply(self._b[:n], self._frac[:n], out=self._b[:n])
        np.add(self._a[:n], self._b[:n], out=dest[:n])
