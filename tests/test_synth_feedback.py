"""Feedback loops through an explicit delay (#206, decision 63).

The feature that justifies the graph existing. Three claims, and each one is
here because it is the claim and not because it is easy to write:

1. **A cable completing a cycle is accepted when the cycle contains a delay
   of at least one block, and refused otherwise.** `tests/test_synth_graph.py`
   covers the straight-line version against fake modules; what is here is the
   cases that are not straight lines -- a loop through the Mix node, a loop
   entirely inside one voice, a delay that is in the patch but not on the
   loop, two loops sharing nodes, and a loop that stops being legal when its
   delay is deleted.

2. **The loop renders as a feedback loop.** Not silence, which is what a
   mis-ordered graph produces, and not a runaway, which is what an
   unclamped-but-unstable one produces. Built out of the real
   `WavetableOscillator`, the real `Delay` and the real `MixModule`, because
   a fake delay here would be assuming the thing under test.

3. **Nothing clamps.** A loop at unity gain or above grows without bound and
   no rule refuses it and no module quietly turns it down (decision 63 §4).
   That is asserted directly, so a later "helpful" limiter fails a test that
   says in its name why it should not exist.
"""

import numpy as np
import pytest

from notecolor.audio.graph import contract, graph
from notecolor.audio.graph.contract import (
    Activation, Module, ModuleDescriptor, NoteContext, audio_in, audio_out,
)
from notecolor.audio.graph.graph import ModuleGraph
from notecolor.audio.graph.modules.delay import Delay
from notecolor.audio.graph.modules.oscillator import WavetableOscillator
from notecolor.audio.graph.poly import MixModule, PolyGraph

SAMPLE_RATE = 44100.0
BLOCK = 512
#: One block of delay at this rate. The floor the module enforces, and so the
#: shortest loop the engine can run (11.61 ms).
ONE_BLOCK_SECONDS = BLOCK / SAMPLE_RATE


class Join(Module):
    """Two inputs, one output, `a + gain * b`.

    The module a feedback patch is actually built out of: something the loop
    comes back *into*. `gain` is the loop gain, which is the number the
    stability claims below turn on, and it is an ordinary attribute rather
    than a parameter because the tests want to read it in the patch
    description rather than set it through a knob.
    """

    def __init__(self, gain=0.5):
        self.gain = gain

    def descriptor(self):
        return ModuleDescriptor("test.join", "Join", poly=contract.POLY_EITHER)

    def ports(self):
        return (audio_in("a", "A"), audio_in("b", "B"), audio_out("out", "Out"))

    def new_instance(self):
        return Join(self.gain)

    def process(self, ctx):
        n = ctx.frames
        np.multiply(ctx.inputs[1][:n], self.gain, out=ctx.outputs[0][:n])
        np.add(ctx.outputs[0][:n], ctx.inputs[0][:n], out=ctx.outputs[0][:n])


class Plain(Module):
    """A module with no block delay: one in, one out, straight through. What
    a loop is refused for containing only these."""

    def __init__(self, module_id="plain"):
        self.module_id = module_id

    def descriptor(self):
        return ModuleDescriptor(self.module_id, self.module_id.title(),
                                poly=contract.POLY_EITHER)

    def ports(self):
        return (audio_in("in"), audio_out("out"))

    def new_instance(self):
        return Plain(self.module_id)

    def process(self, ctx):
        n = ctx.frames
        np.copyto(ctx.outputs[0][:n], ctx.inputs[0][:n])


def echo_patch(gain=0.5, block=BLOCK):
    """Osc -> Join -> Delay -> back into Join, with the loop closed last.

    The smallest patch that is genuinely a feedback loop: the delay's output
    reaches the delay's input again, through a module the graph has to run
    *before* the delay even though it is fed by it.
    """
    g = ModuleGraph()
    g.add("osc", WavetableOscillator("sine"))
    g.add("join", Join(gain))
    g.add("echo", Delay())
    g.activate(Activation(SAMPLE_RATE, block))
    assert g.connect("osc", "out", "join", "a").ok
    assert g.connect("join", "out", "echo", "in").ok
    assert g.connect("echo", "out", "join", "b").ok
    module = g.node("echo").module
    module.params.set("time", ONE_BLOCK_SECONDS)
    module.params.set("feedback", 0.0)   # the loop is the cables', not the line's
    module.params.set("mix", 1.0)
    return g


def block_levels(compiled, node_id, blocks, note, frames=BLOCK):
    """RMS at one node's output, one value per block.

    RMS rather than peak: the peak of a sine inside a 512-frame window
    wobbles with where the window falls, which is not a decay and would make
    a monotonicity assertion a coin toss.
    """
    levels = []
    for _ in range(blocks):
        compiled.process(frames, note=note)
        levels.append(float(np.sqrt(np.mean(compiled.buffer(node_id) ** 2))))
    return levels


# -- claim 1: which cables are accepted --------------------------------------


def test_a_delay_elsewhere_in_the_patch_does_not_legalise_this_loop():
    """The cycle rule is about the delay being **on the cycle**, not about
    one being on the canvas. Easy to get wrong by asking "does this patch
    contain a delay", which is a question with the right answer here and the
    wrong meaning."""
    g = ModuleGraph()
    for node_id in ("a", "b"):
        g.add(node_id, Plain(node_id))
    g.add("aside", Delay())            # in the patch, and irrelevant to the loop
    g.activate(Activation(SAMPLE_RATE, BLOCK))
    g.connect("a", "out", "b", "in")
    g.connect("b", "out", "aside", "in")

    verdict = g.judge("b", "out", "a", "in")
    assert verdict.code == graph.REFUSE_CYCLE
    assert "Delay" in verdict.reason
    # And the refusal names the loop the user made, not the whole patch.
    assert "Aside" not in verdict.reason


def test_two_loops_may_share_one_delay():
    """Overlapping loops, both legal through the same module. Nothing in the
    rule counts loops; it asks each closing cable whether what it closes is
    already out of the execution order."""
    g = ModuleGraph()
    g.add("d", Delay())
    for node_id in ("b", "c"):
        g.add(node_id, Plain(node_id))
    g.add("join", Join())
    g.activate(Activation(SAMPLE_RATE, BLOCK))
    g.connect("join", "out", "d", "in")
    g.connect("d", "out", "b", "in")
    g.connect("d", "out", "c", "in")
    assert g.connect("b", "out", "join", "a").ok
    assert g.connect("c", "out", "join", "b").ok
    # Both loops run: the order exists, which is the only thing "legal" means.
    assert g.order().index("d") == len(g.nodes()) - 1


def test_a_second_loop_without_the_delay_is_still_refused():
    """Nested loops, one legal and one not. The legal one existing must not
    make the illegal one look ordered -- which it would if the rule were
    "this patch has a delay in it somewhere"."""
    g = ModuleGraph()
    g.add("d", Delay())
    g.add("join", Join())
    g.add("b", Plain("b"))
    g.activate(Activation(SAMPLE_RATE, BLOCK))
    g.connect("join", "out", "d", "in")
    assert g.connect("d", "out", "join", "b").ok     # legal loop, through d
    g.connect("join", "out", "b", "in")
    verdict = g.judge("b", "out", "join", "a")       # illegal loop, join -> b -> join
    assert verdict.code == graph.REFUSE_CYCLE


def test_deleting_the_delay_makes_the_standing_loop_illegal_to_redraw():
    """The ticket's "a loop that becomes illegal when the delay is removed".

    Deleting a module takes its cables with it, so the loop is gone rather
    than left dangling -- but the same cable the user drew a moment ago is
    now refused, which is the honest behaviour and the one worth pinning.
    """
    g = echo_patch()
    assert g.loop_members() == {"join", "echo"}
    g.remove("echo")
    assert g.loop_members() == set()
    g.add("plain", Plain("plain"))
    g.connect("join", "out", "plain", "in")
    assert g.judge("plain", "out", "join", "b").code == graph.REFUSE_CYCLE


def test_a_loop_inside_one_voice_is_legal_and_stays_inside_the_voice():
    """A cycle entirely on the per-note side. Nothing about the rule changes
    -- but the delay is now sixteen delays, one per voice, and this is the
    patch that proves `POLY_EITHER` was not just a word in the descriptor."""
    g = ModuleGraph()
    g.add("osc", WavetableOscillator("sine"))
    g.add("join", Join(0.5))
    g.add("echo", Delay())
    g.add("mix", MixModule())
    g.connect("osc", "out", "join", "a")
    g.connect("join", "out", "echo", "in")
    g.connect("echo", "out", "join", "b")
    g.connect("join", "out", "mix", "in")

    p = PolyGraph(g, voices=3).activate(Activation(SAMPLE_RATE, BLOCK))
    assert p.sides()["echo"] == "poly"
    # Sixteen (here three) separate delay lines, not one shared one -- which
    # is the difference between three notes echoing and one note echoing
    # three times as loud.
    lines = {id(p.module("echo", voice=i)) for i in range(3)}
    assert len(lines) == 3


def test_a_loop_through_the_mix_node_is_refused_and_says_which_side_to_pick():
    """Found while building #206. `graph.judge()` accepts this -- correctly,
    since a Delay is on the loop -- but a node that both feeds Mix and is fed
    by it would have to be sixteen copies and one copy at once. The old
    behaviour was worse than a refusal: `PolyGraph` silently dropped the half
    of the loop that crossed the boundary, so the patch ran, sounded like
    nothing, and said nothing."""
    g = ModuleGraph()
    g.add("osc", WavetableOscillator("sine"))
    g.add("mix", MixModule())
    g.add("echo", Delay())
    g.connect("osc", "out", "mix", "in")
    g.connect("mix", "out", "echo", "in")
    p = PolyGraph(g, voices=2)

    assert g.judge("echo", "out", "mix", "in").ok    # the graph alone sees no problem
    verdict = p.judge("echo", "out", "mix", "in")
    assert verdict.code == graph.REFUSE_POLY
    assert "Delay" in verdict.reason and "MIX" in verdict.reason


def test_a_loop_through_mix_forced_into_the_patch_is_caught_at_activate():
    """Same rule at the other end, for a patch file or a test that never
    asked. Refusing to build is the behaviour decision 61 §2 already chose
    for the boundary crossing, for the same reason: silently dropping a
    cable is how a patch stops matching the picture of it."""
    g = ModuleGraph()
    g.add("osc", WavetableOscillator("sine"))
    g.add("mix", MixModule())
    g.add("echo", Delay())
    g.connect("osc", "out", "mix", "in")
    g.connect("mix", "out", "echo", "in")
    g.force_connect("echo", "out", "mix", "in")
    with pytest.raises(contract.ContractError) as excinfo:
        PolyGraph(g, voices=2).activate(Activation(SAMPLE_RATE, BLOCK))
    assert "echo" in str(excinfo.value)


# -- claim 2: the loop renders as a loop -------------------------------------


def test_the_loop_repeats_the_signal_and_decays_at_the_loop_gain():
    """#206's "done when", the direct form: build it, render it, and look at
    what came out.

    A one-block burst goes round the loop and comes back quieter by the loop
    gain each trip. Not silence: a graph that ordered the loop wrong would
    emit nothing at the delay's output forever. Not a runaway: the gain is
    below one, so the tail has to shrink.

    **A trip is two blocks, not one**, and that is worth naming because the
    first draft of this test assumed otherwise. One block is the delay's own
    guarantee. The second is the cable *leaving* it: that cable is what the
    ordering rule cuts, so Join runs before the Delay and reads the output
    buffer the Delay filled last block. A patch-level loop's shortest period
    is therefore the delay time plus one block, and a one-block burst in a
    one-block loop comes out on alternate blocks -- which is exactly the
    silence between the odd entries below.
    """
    g = echo_patch(gain=0.5)
    compiled = g.compile()
    note = NoteContext(frequency=220.0)

    # The oscillator is the excitation; silence it after one block so what is
    # left is only what the loop is carrying.
    compiled.process(BLOCK, note=note)
    g.node("osc").module.params.set("level", 0.0)

    levels = block_levels(compiled, "echo", 8, note)
    assert levels[0] > 0.0, "nothing came back out of the loop"
    assert levels[1] == 0.0, "the burst is between trips, not smeared across them"
    trips = levels[0::2]
    assert all(later < earlier for earlier, later in zip(trips, trips[1:]))
    # A gain of 0.5 per trip, four trips in eight blocks.
    assert trips[3] == pytest.approx(trips[0] * 0.5 ** 3, rel=0.05)


def test_the_delay_is_ordered_last_so_the_loop_has_something_to_read():
    """The ordering rule, seen in a running graph rather than in a list. The
    delay's output cable does not constrain the order, so Join runs *before*
    the module feeding it -- and reads last block's value, which is exactly
    what makes the first block silent and the second not."""
    g = echo_patch()
    compiled = g.compile()
    assert [step.module.descriptor().module_id for step in compiled.steps] == [
        "osc.wavetable.sine", "test.join", "delay.mono"]

    note = NoteContext(frequency=220.0)
    compiled.process(BLOCK, note=note)
    assert np.max(np.abs(compiled.buffer("echo"))) == 0.0
    compiled.process(BLOCK, note=note)
    assert np.max(np.abs(compiled.buffer("echo"))) > 0.0


def test_the_once_only_loop_keeps_ringing_after_the_note_is_gone():
    """The whole patch, through the real Mix node: sixteen voices into Mix,
    a feedback loop on the once-only side. The point of putting the loop
    there is that note-off tears down nothing to the right of Mix (decision
    61 §5), so the loop outlives the key."""
    g = ModuleGraph()
    g.add("osc", WavetableOscillator("sine"))
    g.add("mix", MixModule())
    g.add("join", Join(0.6))
    g.add("echo", Delay())
    g.connect("osc", "out", "mix", "in")
    g.connect("mix", "out", "join", "a")
    g.connect("join", "out", "echo", "in")
    assert g.connect("echo", "out", "join", "b").ok

    p = PolyGraph(g, voices=2).activate(Activation(SAMPLE_RATE, BLOCK))
    p.set_parameter("echo", "time", ONE_BLOCK_SECONDS)
    p.set_parameter("echo", "feedback", 0.0)
    p.set_parameter("echo", "mix", 1.0)

    p.note_on(69)
    for _ in range(4):
        p.process(BLOCK)
    p.all_notes_off()
    p.process(BLOCK)
    assert np.max(np.abs(p.buffer("mix"))) == 0.0, "the voice is gone"

    tail = []
    for _ in range(8):
        p.process(BLOCK)
        tail.append(float(np.sqrt(np.mean(p.buffer("echo") ** 2))))
    assert tail[0] > 0.0, "the loop was torn down with the note"
    # Compared two blocks apart, because a trip round the loop is two blocks
    # -- see `test_the_loop_repeats_the_signal_and_decays_at_the_loop_gain`.
    assert all(later < earlier * 0.7
               for earlier, later in zip(tail, tail[2:]))


# -- claim 3: stability is the patch's business (decision 63 §4) -------------


def test_a_loop_at_unity_gain_or_above_runs_away_and_nothing_stops_it():
    """Decision 63 §4, asserted rather than mentioned.

    This is the test that should fail if anyone ever adds a limiter, a
    clamp, or an auto-gain to the graph or to the Delay. The position is
    every surveyed system's: the engine makes the path legal and audible and
    does not decide the user's levels for them. A patch you can hear going
    wrong is one you can fix; one that was quietly corrected is not.
    """
    g = echo_patch(gain=1.2)
    compiled = g.compile()
    note = NoteContext(frequency=220.0)
    peaks = []
    for _ in range(60):
        compiled.process(BLOCK, note=note)
        peaks.append(float(np.max(np.abs(compiled.buffer("echo")))))
    assert peaks[-1] > 100.0 * peaks[4], "something clamped the loop"
    assert np.isfinite(peaks[-1])          # loud, not broken -- yet
    assert g.node("echo").module.nonfinite_blocks == 0


def test_nothing_refuses_the_runaway_cable_when_it_is_drawn():
    """And the refusal contract stays out of it: loop gain is a knob value
    and a filter's response, not a property of the cable being held, so a
    rule that tried to refuse this would have to guess."""
    g = ModuleGraph()
    g.add("join", Join(4.0))
    g.add("echo", Delay())
    g.activate(Activation(SAMPLE_RATE, BLOCK))
    g.connect("join", "out", "echo", "in")
    assert g.connect("echo", "out", "join", "b").ok
