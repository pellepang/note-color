"""External CC (issue #173, decision 72): a mod-wheel-shaped modulation
source with nothing behind it but a live value someone else writes.

`docs/research/midi-hardware-input.md` §4 found the mod wheel a real but
small addition: `ParamBlock`'s scalar-or-buffer split and `graph.ModRoute`
don't care where a `PORT_MOD` signal comes from -- `Lfo`/`ModEnvelope` are
just two existing sources. This is a third: a `POLY_ONCE` module (a mod
wheel is one physical lever, not per-voice) exposing one `mod_out` port
whose value is whatever `set_value()` was last called with, held flat for
the whole block -- no waveform, no envelope, nothing generated. `set_value()`
is meant to be called by whatever owns the live MIDI CC value
(`gui/patch_bridge.PatchBridge`, fed by `audio/midi_input.MidiDispatcher`'s
`mod_wheel` callback), off the audio thread, same "a plain float write is
inaudibly stale by at most one block" reasoning `ParamBlock.values` already
relies on for a knob edit.

**Reachable from the canvas since issue #235.** `gui/patch_bridge.
MODULE_FACTORIES`/`MOD_SOURCE_PORTS` name this module (`"midi_cc"`) so a
patch built directly against the engine (this module's own tests, and any
future caller) can use it exactly like `lfo`/`mod_env`; the drawer entry
that drags one out ("Mod Wheel", `gui/synth_workspace.SYNTH_CORE_MODULES`)
followed once `gui/patch_graph.py` was free of the concurrent #227 work
this ticket's v1 had deliberately left it to.
"""

from __future__ import annotations

from notecolor.audio.graph import contract
from notecolor.audio.graph.contract import Module, ModuleDescriptor, mod_out


class ExternalCc(Module):
    """One live external value (a MIDI CC, most commonly the mod wheel),
    as a modulation source. `descriptor().poly` is always `POLY_ONCE` --
    unlike `Lfo`, there is no per-note variant to choose: one physical
    lever, one value, broadcast to whichever knob it is cabled to (per-note
    or global -- `poly.PolyGraph`'s existing mod-crossing machinery handles
    a once-only source reaching a per-note knob exactly as it already does
    for a global LFO)."""

    def __init__(self):
        self._value = 0.0

    def descriptor(self):
        return ModuleDescriptor(
            module_id="midi_cc",
            name="External CC",
            poly=contract.POLY_ONCE,
            category="modulator",
        )

    def ports(self):
        return (mod_out("mod", "Mod"),)

    def _allocate(self, activation):
        self._mod_index = self.port_index("mod", contract.DIRECTION_OUT)

    def set_value(self, value):
        """0..1 (a MIDI CC's own normalized range). Clamped, so a caller
        passing a raw out-of-range float cannot hand the audio thread a
        modulation depth outside what every destination's own `ParamSpec`
        range already assumes. Safe to call from any thread that is not
        the audio thread -- a single Python float attribute write, the
        same "one bytecode op, staleness of at most one block is
        inaudible" reasoning `ParamBlock.values` already relies on
        (contract.py's own docstring)."""
        self._value = min(1.0, max(0.0, float(value)))

    def process(self, ctx):
        n = ctx.frames
        if n <= 0:
            return
        ctx.outputs[self._mod_index][:n] = self._value
