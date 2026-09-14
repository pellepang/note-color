"""The graph: execution order, the cycle rule, and every refusal (#203).

Against fake modules, deliberately. The graph's job is ordering and rules,
and a test that proved it by listening to an oscillator would be proving the
oscillator. The fakes below do arithmetic simple enough to read an expected
value straight out of the test -- which is what makes "did this run before
that" answerable at all.

Two tests at the end use the real `Delay`, because the claim there is about
a feedback loop actually running and staying finite, and a fake delay would
be assuming the thing under test.
"""

import gc
import tracemalloc

import numpy as np
import pytest

from notecolor.audio.graph import contract, graph
from notecolor.audio.graph.contract import (
    Activation, Module, ModuleDescriptor, ParamSpec, PortSpec, audio_in, audio_out,
    mod_in, mod_out,
)
from notecolor.audio.graph.graph import ModuleGraph
from notecolor.audio.graph.modules.delay import Delay
from notecolor.audio.graph.modules.oscillator import WavetableOscillator

SAMPLE_RATE = 44100.0
BLOCK = 64


class Fake(Module):
    """A module of any shape, that records when it ran.

    `process()` writes `bias + gain * (sum of its inputs)`, so a chain's
    output is a number a reader can work out by hand, and appends its own id
    to a shared list, so ordering is directly observable.
    """

    def __init__(self, module_id="fake", *, ports=None, poly=contract.POLY_EITHER,
                 block_delay=0, bias=0.0, gain=1.0, log=None):
        self.module_id = module_id
        self._ports = tuple(ports if ports is not None
                            else (audio_in("in"), audio_out("out")))
        self._poly = poly
        self._block_delay = block_delay
        self.bias = bias
        self.gain = gain
        self.log = log if log is not None else []

    def descriptor(self):
        return ModuleDescriptor(self.module_id, self.module_id.title(),
                                poly=self._poly, block_delay=self._block_delay)

    def ports(self):
        return self._ports

    def parameters(self):
        return (ParamSpec("amount", "Amount", 0.0, 1.0, 1.0),)

    def _allocate(self, activation):
        self.activations = getattr(self, "activations", 0) + 1
        # The fake obeys the no-allocation rule too, or the graph's own
        # allocation test would be measuring this class instead.
        self._scratch = np.zeros(activation.max_block, dtype=np.float64)

    def process(self, ctx):
        self.log.append(self.module_id)
        n = ctx.frames
        for out in ctx.outputs:
            out[:n] = self.bias
            for source in ctx.inputs:
                np.multiply(source[:n], self.gain, out=self._scratch[:n])
                np.add(out[:n], self._scratch[:n], out=out[:n])


def source(module_id="src", bias=1.0, poly=contract.POLY_EITHER, log=None):
    return Fake(module_id, ports=(audio_out("out"),), bias=bias, poly=poly, log=log)


def built(*nodes, activate=True, block=BLOCK):
    """A `ModuleGraph` holding `(node_id, module)` pairs, activated."""
    g = ModuleGraph()
    for node_id, module in nodes:
        g.add(node_id, module)
    if activate:
        g.activate(Activation(SAMPLE_RATE, block))
    return g


# -- construction ------------------------------------------------------------


def test_a_node_added_after_activation_is_activated_too():
    g = built(("a", source()))
    late = Fake("late")
    g.add("b", late)
    assert late.activation is not None


def test_two_nodes_cannot_share_an_id():
    g = built(("a", source()))
    with pytest.raises(contract.ContractError):
        g.add("a", source())


def test_removing_a_module_takes_its_cables_with_it():
    g = built(("a", source()), ("b", Fake("b")), ("c", Fake("c")))
    g.connect("a", "out", "b", "in")
    g.connect("b", "out", "c", "in")
    assert g.remove("b")
    assert g.connections == []
    assert g.order() == ["a", "c"]


def test_every_edit_bumps_the_revision():
    # How a host tells that the CompiledGraph it is holding is stale,
    # without comparing graphs.
    g = built(("a", source()), ("b", Fake("b")))
    seen = [g.revision]
    g.connect("a", "out", "b", "in")
    seen.append(g.revision)
    g.disconnect("a", "out", "b", "in")
    seen.append(g.revision)
    assert seen == sorted(set(seen))


# -- refusals ----------------------------------------------------------------


def test_a_refused_cable_is_not_plugged_in():
    g = built(("a", source()), ("b", Fake("b")))
    g.connect("a", "out", "b", "in")
    verdict = g.connect("a", "out", "b", "in")
    assert verdict.code == graph.REFUSE_DUPLICATE
    assert len(g.connections) == 1


def test_sound_into_a_knob_says_which_cable_you_are_holding():
    g = built(("a", source()),
              ("b", Fake("b", ports=(mod_in("cv"), audio_out("out")))))
    verdict = g.judge("a", "out", "b", "cv")
    assert verdict.code == graph.REFUSE_TYPE
    assert "sends sound" in verdict.reason and "knob movement" in verdict.reason


def test_knob_movement_into_a_sound_socket_is_refused_the_other_way():
    g = built(("lfo", Fake("lfo", ports=(mod_out("mod"),))), ("b", Fake("b")))
    verdict = g.judge("lfo", "mod", "b", "in")
    assert verdict.code == graph.REFUSE_TYPE
    assert "knob movement" in verdict.reason


def test_an_output_dropped_on_an_output_is_a_direction_refusal():
    g = built(("a", source()), ("b", Fake("b")))
    assert g.judge("a", "out", "b", "out").code == graph.REFUSE_DIRECTION
    assert g.judge("b", "in", "a", "out").code == graph.REFUSE_DIRECTION


def test_a_module_cannot_feed_itself_and_is_told_why():
    g = built(("a", Fake("a")))
    verdict = g.judge("a", "out", "a", "in")
    assert verdict.code == graph.REFUSE_SELF
    assert "Delay" in verdict.reason


def test_per_note_into_once_only_points_at_mix():
    # Decision 56 §3: refused, not silently summed. Reaktor's behaviour.
    g = built(("voice", source(poly=contract.POLY_PER_NOTE)),
              ("verb", Fake("verb", poly=contract.POLY_ONCE)))
    verdict = g.judge("voice", "out", "verb", "in")
    assert verdict.code == graph.REFUSE_POLY
    assert "MIX" in verdict.reason


def test_once_only_into_per_note_is_not_the_refused_direction():
    g = built(("bus", source(poly=contract.POLY_ONCE)),
              ("voice", Fake("voice", poly=contract.POLY_PER_NOTE)))
    assert g.judge("bus", "out", "voice", "in").ok


def test_either_sided_modules_cross_freely():
    g = built(("a", source(poly=contract.POLY_PER_NOTE)),
              ("b", Fake("b", poly=contract.POLY_EITHER)),
              ("c", Fake("c", poly=contract.POLY_ONCE)))
    assert g.judge("a", "out", "b", "in").ok
    assert g.judge("b", "out", "c", "in").ok


def test_a_missing_node_or_port_refuses_rather_than_raising():
    g = built(("a", source()))
    assert g.judge("a", "out", "ghost", "in").code == graph.REFUSE_NO_SUCH_NODE
    assert g.judge("a", "nope", "a", "in").code == graph.REFUSE_NO_SUCH_PORT


# -- the cycle rule ----------------------------------------------------------


def test_a_loop_with_no_delay_is_refused_and_names_the_whole_loop():
    g = built(("a", Fake("a")), ("b", Fake("b")), ("c", Fake("c")))
    g.connect("a", "out", "b", "in")
    g.connect("b", "out", "c", "in")
    verdict = g.judge("c", "out", "a", "in")
    assert verdict.code == graph.REFUSE_CYCLE
    for name in ("A", "B", "C"):
        assert name in verdict.reason
    assert "Delay" in verdict.reason


def test_the_same_loop_is_legal_once_a_delay_is_in_it():
    # Decision 56 §4, Bitwig's rule -- and the test the ticket asks for.
    g = built(("a", Fake("a")), ("b", Fake("b")),
              ("d", Fake("d", block_delay=1)))
    g.connect("a", "out", "b", "in")
    g.connect("b", "out", "d", "in")
    assert g.connect("d", "out", "a", "in").ok

    order = g.order()
    # The delay's *input* still constrains: everything feeding it runs
    # first. Its *output* does not, which is what makes the loop orderable.
    assert order.index("a") < order.index("b") < order.index("d")


def test_a_delay_may_feed_itself():
    g = built(("d", Fake("d", block_delay=1)))
    assert g.connect("d", "out", "d", "in").code == graph.REFUSE_SELF


def test_loop_members_are_reported_over_the_real_cables_not_the_ordering():
    # The canvas colours the loop the user made, which includes the delay
    # edge the execution order deliberately ignores.
    g = built(("a", Fake("a")), ("d", Fake("d", block_delay=1)))
    g.connect("a", "out", "d", "in")
    g.connect("d", "out", "a", "in")
    assert g.loop_members() == {"a", "d"}


def test_a_patch_with_no_loop_reports_no_loop_members():
    g = built(("a", source()), ("b", Fake("b")))
    g.connect("a", "out", "b", "in")
    assert g.loop_members() == set()


def test_an_illegal_cycle_forced_past_judge_is_caught_at_compile():
    g = built(("a", Fake("a")), ("b", Fake("b")))
    g.force_connect("a", "out", "b", "in")
    g.force_connect("b", "out", "a", "in")
    with pytest.raises(graph.CycleError) as excinfo:
        g.compile()
    assert "a" in str(excinfo.value) and "b" in str(excinfo.value)


# -- execution order ---------------------------------------------------------


def test_a_chain_runs_in_signal_order():
    log = []
    g = built(("c", Fake("c", log=log)), ("a", source("a", log=log)), ("b", Fake("b", log=log)))
    g.connect("a", "out", "b", "in")
    g.connect("b", "out", "c", "in")
    compiled = g.compile()
    compiled.process(BLOCK)
    assert log == ["a", "b", "c"]


def test_a_diamond_runs_both_arms_before_the_join():
    log = []
    g = built(("src", source("src", log=log)), ("l", Fake("l", log=log)),
              ("r", Fake("r", log=log)),
              ("join", Fake("join", ports=(audio_in("a"), audio_in("b"), audio_out("out")),
                            log=log)))
    g.connect("src", "out", "l", "in")
    g.connect("src", "out", "r", "in")
    g.connect("l", "out", "join", "a")
    g.connect("r", "out", "join", "b")
    g.compile().process(BLOCK)
    assert log[0] == "src" and log[-1] == "join"


def test_the_order_is_deterministic_across_identical_compiles():
    # A graph that reorders itself between two identical compiles makes
    # every ordering bug unreproducible.
    def build():
        g = built(("a", source()), ("b", Fake("b")), ("c", Fake("c")), ("d", Fake("d")))
        g.connect("a", "out", "b", "in")
        g.connect("a", "out", "c", "in")
        g.connect("b", "out", "d", "in")
        return g.order()

    assert build() == build()


def test_an_isolated_module_still_runs():
    # An unpatched module is not an error, and its knobs still do something
    # the moment a cable arrives; skipping it would make patching order
    # audible.
    log = []
    g = built(("a", source("a", log=log)), ("lonely", Fake("lonely", log=log)))
    g.compile().process(BLOCK)
    assert set(log) == {"a", "lonely"}


# -- buffers -----------------------------------------------------------------


def test_an_unconnected_input_reads_silence():
    g = built(("b", Fake("b", bias=0.0)))
    compiled = g.compile()
    compiled.process(BLOCK)
    assert np.all(compiled.buffer("b") == 0.0)


def test_one_cable_binds_straight_through_with_no_copy():
    # A copy per cable per block is pure cost: no module writes to its
    # inputs, so the consumer can read the producer's own buffer.
    g = built(("a", source()), ("b", Fake("b")))
    g.connect("a", "out", "b", "in")
    compiled = g.compile()
    step = next(s for s in compiled.steps if s.module.module_id == "b")
    assert step.ctx.inputs[0] is compiled.buffer("a")
    assert step.sums == ()


def test_several_cables_on_one_input_are_summed():
    g = built(("a", source(bias=1.0)), ("b", source("b", bias=2.0)),
              ("sink", Fake("sink", bias=0.0)))
    g.connect("a", "out", "sink", "in")
    g.connect("b", "out", "sink", "in")
    compiled = g.compile()
    compiled.process(BLOCK)
    assert np.all(compiled.buffer("sink") == 3.0)


def test_a_chain_carries_a_value_end_to_end_in_one_block():
    # Everything downstream sees this block's value, not last block's --
    # the direct consequence of running in topological order.
    g = built(("a", source(bias=2.0)), ("b", Fake("b", bias=1.0, gain=3.0)))
    g.connect("a", "out", "b", "in")
    compiled = g.compile()
    compiled.process(BLOCK)
    assert np.all(compiled.buffer("b") == 1.0 + 3.0 * 2.0)


# -- staged and swapped ------------------------------------------------------


def test_compiling_does_not_disturb_the_graph_already_playing():
    """The whole of the swap design: an edit builds a second
    `CompiledGraph`, and the one the callback is holding keeps working
    until the host rebinds a single attribute."""
    g = built(("a", source(bias=1.0)), ("b", Fake("b", bias=0.0)))
    g.connect("a", "out", "b", "in")
    playing = g.compile()
    playing.process(BLOCK)
    assert np.all(playing.buffer("b") == 1.0)

    g.add("c", source("c", bias=5.0))
    g.connect("c", "out", "b", "in")
    pending = g.compile()

    playing.process(BLOCK)
    assert np.all(playing.buffer("b") == 1.0)  # unchanged by the edit
    pending.process(BLOCK)
    assert np.all(pending.buffer("b") == 6.0)
    assert playing.revision != pending.revision


def test_compile_before_activate_is_a_contract_error():
    g = built(("a", source()), activate=False)
    with pytest.raises(contract.ContractError):
        g.compile()


# -- with the real delay -----------------------------------------------------


def test_a_feedback_loop_through_a_real_delay_runs_and_stays_finite():
    """The feature that justifies the graph existing (decision 56 §8).

    An oscillator into a delay, the delay's output folded back into its own
    input through an ordinary gain module -- a cable the rules refuse
    without the delay in the path. It has to run, and it has to not blow up.
    """
    g = ModuleGraph()
    g.add("osc", WavetableOscillator("sine"))
    # Stands in for #204's Mix node only in *shape*: this ticket has no Mix
    # node, so the join declares POLY_EITHER and the boundary rule tested
    # above is not what is under test here.
    # Loop gain below unity, so the echo decays. Whether a patch is stable
    # is the patch's business and not the graph's -- at gain 1.0 through the
    # same cables this runs away, correctly, and nothing refuses it.
    g.add("mix", Fake("mix", ports=(audio_in("a"), audio_in("b"), audio_out("out")),
                      poly=contract.POLY_EITHER, gain=0.4))
    g.add("echo", Delay())
    g.activate(Activation(SAMPLE_RATE, 512))

    assert g.connect("osc", "out", "mix", "a").ok
    assert g.connect("mix", "out", "echo", "in").ok
    assert g.connect("echo", "out", "mix", "b").ok  # closes the loop

    g.node("echo").module.params.set("time", 512 / SAMPLE_RATE)
    g.node("echo").module.params.set("feedback", 0.0)
    g.node("echo").module.params.set("mix", 1.0)

    compiled = g.compile()
    assert compiled.steps[0].module.descriptor().module_id.startswith("osc.")

    note = contract.NoteContext(frequency=220.0)
    peaks = []
    for _ in range(20):
        compiled.process(512, note=note)
        peaks.append(float(np.max(np.abs(compiled.buffer("echo")))))
    # Block 1 is silent at the delay's output and block 2 is not: the
    # one-block guarantee, visible in a running graph rather than in a unit
    # test of the module. That silence is exactly what the loop is ordered
    # around.
    assert peaks[0] == 0.0
    assert peaks[1] > 0.0
    assert max(peaks) < 2.0, "the loop ran away"

    # Silence the oscillator and the loop should ring on, quieter each time
    # round -- which is the audible proof that sound really is travelling
    # back through the delay rather than just passing through it.
    g.node("osc").module.params.set("level", 0.0)
    # Measured as RMS per block, not peak: the peak of a 220Hz sine inside a
    # 512-frame window wobbles in the fourth decimal with where the window
    # falls, which is not a decay and would make the assertion below a
    # coin toss.
    tail = []
    for _ in range(6):
        compiled.process(512, note=note)
        tail.append(float(np.sqrt(np.mean(compiled.buffer("echo") ** 2))))
    assert tail[1] > 0.0
    assert tail[-1] < tail[1] * 0.2, f"the echo did not decay: {tail}"


def test_the_loop_is_refused_the_moment_the_delay_leaves_it():
    g = ModuleGraph()
    g.add("osc", WavetableOscillator("sine"))
    # Stands in for #204's Mix node only in *shape*: this ticket has no Mix
    # node, so the join declares POLY_EITHER and the boundary rule tested
    # above is not what is under test here.
    g.add("mix", Fake("mix", ports=(audio_in("a"), audio_in("b"), audio_out("out")),
                      poly=contract.POLY_EITHER))
    g.add("echo", Delay())
    g.activate(Activation(SAMPLE_RATE, 512))
    g.connect("osc", "out", "mix", "a")
    g.connect("mix", "out", "echo", "in")
    g.remove("echo")
    # Same shape, no delay: the cable that was legal a moment ago is not.
    g.add("gain", Fake("gain", poly=contract.POLY_EITHER))
    g.connect("mix", "out", "gain", "in")
    verdict = g.judge("gain", "out", "mix", "b")
    assert verdict.code == graph.REFUSE_CYCLE


# -- the audio thread's own rule ---------------------------------------------


def peak_bytes(compiled, frames, note, reps=32):
    for _ in range(4):
        compiled.process(frames, note=note)
    gc.collect()
    tracemalloc.start()
    for _ in range(4):
        compiled.process(frames, note=note)
    tracemalloc.reset_peak()
    base = tracemalloc.get_traced_memory()[0]
    for _ in range(reps):
        compiled.process(frames, note=note)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    return peak - base


def test_running_the_graph_allocates_nothing_that_scales_with_the_block():
    """Contract rule 2 again, this time for the graph's own per-block work
    -- the input summing and the context rebinding, which is the part
    `test_synth_graph_contract.py` cannot see. Same peak-versus-block-size
    method, same reason it has to be peak: a temporary is freed before any
    snapshot could notice it."""
    def compiled_for(block):
        g = ModuleGraph()
        g.add("osc", WavetableOscillator("saw"))
        g.add("osc2", WavetableOscillator("square"))
        g.add("sum", Fake("sum", poly=contract.POLY_EITHER))
        g.add("echo", Delay())
        g.activate(Activation(SAMPLE_RATE, 2048))
        g.connect("osc", "out", "sum", "in")
        g.connect("osc2", "out", "sum", "in")   # two cables, one input: a sum step
        g.connect("sum", "out", "echo", "in")
        return g.compile()

    note = contract.NoteContext(frequency=220.0)
    grew = peak_bytes(compiled_for(2048), 2048, note) - peak_bytes(compiled_for(64), 64, note)
    assert grew < 16 * 1024, f"the graph grew {grew} bytes with the block"


# -- where the summing happens (decision 60 §3, the owner's call) -------------


def test_the_graph_publishes_which_jacks_sum():
    """Inputs sum, and the canvas marks the jack. Published by the graph
    rather than counted by the canvas, so the two can never disagree about
    where addition is happening."""
    g = built(("a", source("a")), ("b", source("b")), ("c", source("c")),
              ("sink", Fake("sink", ports=(audio_in("x"), audio_in("y"), audio_out("out")))))
    g.connect("a", "out", "sink", "x")
    assert g.summed_inputs() == {}
    g.connect("b", "out", "sink", "x")
    g.connect("c", "out", "sink", "y")
    assert g.summed_inputs() == {("sink", "x"): 2}


def test_unplugging_back_to_one_cable_stops_marking_the_jack():
    g = built(("a", source("a")), ("b", source("b")), ("sink", Fake("sink")))
    g.connect("a", "out", "sink", "in")
    g.connect("b", "out", "sink", "in")
    assert g.summed_inputs() == {("sink", "in"): 2}
    g.disconnect("b", "out", "sink", "in")
    assert g.summed_inputs() == {}
