"""The Mix node and the poly boundary (#204, decision 56 §3).

The four claims the ticket asks to be shown, and they are the four that
would actually break a synth:

- sixteen per-note chains sum to one signal, and each chain is genuinely its
  own copy rather than one module played sixteen times;
- a released voice is torn down and its slot comes back;
- a delay on the once-only side keeps its tail past note-off, because
  note-off tears down nothing to the right of Mix;
- a cable crossing the boundary the wrong way is refused, with a reason.

Headless. The fakes are the same idea as `test_synth_graph.py`'s: arithmetic
simple enough that the expected number can be read straight out of the test.
"""

import numpy as np
import pytest

from notecolor.audio.graph import contract, graph, poly
from notecolor.audio.graph.contract import (
    Activation, Module, ModuleDescriptor, ParamSpec, audio_in, audio_out,
)
from notecolor.audio.graph.graph import ModuleGraph
from notecolor.audio.graph.modules.delay import Delay
from notecolor.audio.graph.modules.oscillator import WavetableOscillator
from notecolor.audio.graph.poly import MixModule, PolyGraph, SIDE_MONO, SIDE_POLY

SAMPLE_RATE = 44100.0
BLOCK = 64


class Tone(Module):
    """A per-note source that writes its note's own frequency as a constant.

    Not musical, deliberately: a constant is the easiest possible way to see
    *which* note a voice is rendering, and whether sixteen of them really
    summed.
    """

    def descriptor(self):
        return ModuleDescriptor("test.tone", "Tone", poly=contract.POLY_PER_NOTE,
                                category="source")

    def ports(self):
        return (audio_out("out"),)

    def parameters(self):
        return (ParamSpec("level", "Level", 0.0, 1.0, 1.0),)

    def process(self, ctx):
        n = ctx.frames
        value = ctx.note.frequency if ctx.note is not None else 0.0
        ctx.outputs[0][:n] = value * ctx.params[0] * (1.0 if ctx.note.gate else 0.5)


class Envelope(Module):
    """Stands in for #205's amp envelope: passes sound through while the gate
    is up, and on release counts `tail` blocks down to silence and then sets
    `note.finished`.

    It exists because voice tear-down is *a module's* decision in this
    contract, not the voice manager's -- see `poly.py`'s docstring on why.
    """

    def __init__(self, tail=2):
        self.tail = tail
        self._left = None

    def descriptor(self):
        return ModuleDescriptor("test.env", "Env", poly=contract.POLY_PER_NOTE)

    def ports(self):
        return (audio_in("in"), audio_out("out"))

    def new_instance(self):
        return Envelope(self.tail)

    def reset(self):
        self._left = None

    def process(self, ctx):
        n = ctx.frames
        if ctx.note.gate:
            self._left = None
            ctx.outputs[0][:n] = ctx.inputs[0][:n]
            return
        if self._left is None:
            self._left = self.tail
        if self._left <= 0:
            ctx.outputs[0][:n] = 0.0
            ctx.note.finished = True
            return
        self._left -= 1
        ctx.outputs[0][:n] = ctx.inputs[0][:n]


class Gain(Module):
    """A once-only module, for the right of Mix."""

    def __init__(self, gain=1.0):
        self.gain = gain

    def descriptor(self):
        return ModuleDescriptor("test.gain", "Gain", poly=contract.POLY_ONCE)

    def ports(self):
        return (audio_in("in"), audio_out("out"))

    def new_instance(self):
        return Gain(self.gain)

    def process(self, ctx):
        n = ctx.frames
        np.multiply(ctx.inputs[0][:n], self.gain, out=ctx.outputs[0][:n])


def patch(voices=4, tail=2, mono=None, block=BLOCK):
    """Tone -> Env -> MIX -> (optional once-only module), activated."""
    g = ModuleGraph()
    g.add("tone", Tone())
    g.add("env", Envelope(tail))
    g.add("mix", MixModule())
    g.connect("tone", "out", "env", "in")
    g.connect("env", "out", "mix", "in")
    if mono is not None:
        g.add("out", mono)
        g.connect("mix", "out", "out", "in")
    return PolyGraph(g, voices=voices).activate(Activation(SAMPLE_RATE, block))


# -- the split ---------------------------------------------------------------


def test_the_cables_decide_which_side_a_module_is_on():
    p = patch(mono=Gain())
    sides = p.sides()
    assert sides["tone"] == SIDE_POLY and sides["env"] == SIDE_POLY
    assert sides["out"] == SIDE_MONO
    assert "mix" not in sides  # the boundary belongs to neither side


def test_an_unpatched_module_lands_on_the_side_it_declares():
    g = ModuleGraph()
    g.add("mix", MixModule())
    g.add("lonely_poly", Tone())
    g.add("lonely_mono", Gain())
    sides = PolyGraph(g, voices=2).sides()
    assert sides["lonely_poly"] == SIDE_POLY
    assert sides["lonely_mono"] == SIDE_MONO


def test_a_patch_needs_exactly_one_mix_node():
    g = ModuleGraph()
    g.add("a", Tone())
    with pytest.raises(contract.ContractError):
        PolyGraph(g)
    g.add("mix", MixModule())
    g.add("mix2", MixModule())
    with pytest.raises(contract.ContractError):
        PolyGraph(g)


# -- sixteen voices ----------------------------------------------------------


def test_every_voice_gets_its_own_copy_of_every_per_note_module():
    # The difference between sixteen voices and one voice sixteen times as
    # loud: sixteen filters with sixteen histories.
    p = patch(voices=4)
    modules = [p.module("env", voice=i) for i in range(4)]
    assert len({id(m) for m in modules}) == 4
    assert all(m is not p.graph.node("env").module for m in modules)


def test_sixteen_held_notes_sum_at_mix():
    p = patch(voices=16)
    for pitch in range(60, 76):
        p.note_on(pitch, frequency=float(pitch))
    p.process(BLOCK)
    assert np.allclose(p.buffer("mix"), sum(range(60, 76)))


def test_the_once_only_side_runs_once_over_the_summed_signal():
    p = patch(voices=4, mono=Gain(0.5))
    p.note_on(60, frequency=10.0)
    p.note_on(64, frequency=20.0)
    p.process(BLOCK)
    assert np.allclose(p.buffer("mix"), 30.0)
    assert np.allclose(p.buffer("out"), 15.0)


def test_a_voice_renders_its_own_note_not_the_last_one():
    p = patch(voices=4)
    p.note_on(60, frequency=100.0)
    p.note_on(72, frequency=200.0)
    p.process(BLOCK)
    voices = {v.note.pitch: v for v in p.active_voices}
    assert np.allclose(voices[60].compiled.buffer("tone"), 100.0)
    assert np.allclose(voices[72].compiled.buffer("tone"), 200.0)


def test_silence_when_nothing_is_held():
    p = patch(voices=4, mono=Gain())
    p.process(BLOCK)
    assert np.all(p.buffer("mix") == 0.0)
    assert np.all(p.buffer("out") == 0.0)


def test_a_knob_edit_reaches_every_voice():
    p = patch(voices=4)
    p.set_parameter("tone", "level", 0.5)
    p.note_on(60, frequency=100.0)
    p.note_on(64, frequency=100.0)
    p.process(BLOCK)
    assert np.allclose(p.buffer("mix"), 100.0)  # two voices at half level


# -- voice lifecycle ---------------------------------------------------------


def test_a_released_voice_gives_its_slot_back():
    p = patch(voices=2, tail=1)
    p.note_on(60, frequency=1.0)
    assert len(p.active_voices) == 1
    p.note_off(60)
    p.process(BLOCK)   # tail block
    assert len(p.active_voices) == 1
    p.process(BLOCK)   # finished
    assert p.active_voices == []


def test_a_voice_that_nothing_releases_drones():
    """With no envelope patched, a released note keeps sounding -- which is
    what a modular with no envelope does, and is deliberately not papered
    over by the voice manager (see `poly.py`)."""
    g = ModuleGraph()
    g.add("tone", Tone())
    g.add("mix", MixModule())
    g.connect("tone", "out", "mix", "in")
    p = PolyGraph(g, voices=2).activate(Activation(SAMPLE_RATE, BLOCK))
    p.note_on(60, frequency=8.0)
    p.note_off(60)
    for _ in range(10):
        p.process(BLOCK)
    assert len(p.active_voices) == 1
    assert np.any(p.buffer("mix") != 0.0)


def test_a_restarted_slot_does_not_inherit_the_old_note_s_state():
    p = patch(voices=1, tail=0)
    p.note_on(60, frequency=1.0)
    p.note_off(60)
    p.process(BLOCK)
    assert p.active_voices == []
    p.note_on(72, frequency=5.0)
    p.process(BLOCK)
    assert np.allclose(p.buffer("mix"), 5.0)


def test_running_out_of_slots_steals_the_oldest_released_voice_first():
    p = patch(voices=2, tail=99)
    first = p.note_on(60, frequency=1.0)
    second = p.note_on(62, frequency=2.0)
    p.note_off(60)          # first is released but still sounding
    third = p.note_on(64, frequency=4.0)
    assert third is first   # the released one, not the older sounding one
    assert second.active and second.note.pitch == 62


def test_stealing_falls_back_to_the_oldest_when_nothing_is_released():
    p = patch(voices=2, tail=99)
    first = p.note_on(60, frequency=1.0)
    p.note_on(62, frequency=2.0)
    assert p.note_on(64, frequency=4.0) is first


def test_all_notes_off_clears_every_slot():
    p = patch(voices=4)
    p.note_on(60, frequency=1.0)
    p.note_on(64, frequency=1.0)
    p.all_notes_off()
    assert p.active_voices == []


def test_note_off_only_releases_the_pitch_asked_for():
    p = patch(voices=4, tail=99)
    p.note_on(60, frequency=1.0)
    p.note_on(64, frequency=1.0)
    assert p.note_off(60) == 1
    gates = {v.note.pitch: v.note.gate for v in p.active_voices}
    assert gates == {60: False, 64: True}


# -- the tail ----------------------------------------------------------------


def test_a_delay_right_of_mix_keeps_its_tail_past_note_off():
    """The practical reason the boundary is worth drawing: the two sides
    have different lifetimes. Tearing a voice down must not touch the echo
    it already put into a once-only delay."""
    echo = Delay()
    p = patch(voices=2, tail=0, mono=echo, block=512)
    p.set_parameter("out", "time", 512 / SAMPLE_RATE)
    p.set_parameter("out", "feedback", 0.6)
    p.set_parameter("out", "mix", 1.0)

    p.note_on(60, frequency=1.0)
    for _ in range(2):
        p.process(512)
    assert np.any(p.buffer("out") != 0.0)

    p.note_off(60)
    p.process(512)
    assert p.active_voices == [], "the voice should be gone"
    assert np.all(p.buffer("mix") == 0.0), "and silent at Mix"

    # ...but the delay, which nothing tore down, is still ringing.
    p.process(512)
    still_ringing = float(np.max(np.abs(p.buffer("out"))))
    assert still_ringing > 0.0
    p.process(512)
    assert 0.0 < float(np.max(np.abs(p.buffer("out")))) < still_ringing


# -- the refusal -------------------------------------------------------------


def test_a_cable_crossing_the_boundary_the_wrong_way_is_refused():
    p = patch(voices=2, mono=Gain())
    verdict = p.judge("env", "out", "out", "in")
    assert verdict.code == graph.REFUSE_POLY
    assert "MIX" in verdict.reason
    assert p.connect("env", "out", "out", "in").ok is False


def test_a_declared_per_note_module_is_refused_by_the_graph_before_sides_matter():
    # `graph.judge()` catches the declared case on its own; `PolyGraph`
    # only has to add the ones that are per-note *because of the patch*.
    p = patch(voices=2, mono=Gain())
    assert p.judge("tone", "out", "out", "in").code == graph.REFUSE_POLY


def test_per_note_into_mix_is_exactly_what_mix_is_for():
    p = patch(voices=2)
    g = p.graph
    g.add("tone2", Tone())
    assert g.judge("tone2", "out", "mix", "in").ok


def test_a_crossing_forced_into_the_patch_is_caught_at_activate():
    g = ModuleGraph()
    g.add("a", WavetableOscillator("sine"))
    g.add("mix", MixModule())
    g.add("out", Gain())
    g.connect("a", "out", "mix", "in")
    g.force_connect("a", "out", "out", "in")
    with pytest.raises(contract.ContractError) as excinfo:
        PolyGraph(g, voices=2).activate(Activation(SAMPLE_RATE, BLOCK))
    assert "without going through Mix" in str(excinfo.value)


# -- with real modules -------------------------------------------------------


def test_sixteen_real_oscillators_sum_to_something_louder_than_one():
    g = ModuleGraph()
    g.add("osc", WavetableOscillator("sine"))
    g.add("mix", MixModule())
    g.connect("osc", "out", "mix", "in")
    p = PolyGraph(g, voices=16).activate(Activation(SAMPLE_RATE, 512))

    p.note_on(69, frequency=440.0)
    p.process(512)
    one = float(np.sqrt(np.mean(p.buffer("mix") ** 2)))

    for i, pitch in enumerate(range(60, 75)):
        p.note_on(pitch, frequency=110.0 * (1.0 + 0.1 * i))
    p.process(512)
    many = float(np.sqrt(np.mean(p.buffer("mix") ** 2)))
    assert many > one * 2.0
