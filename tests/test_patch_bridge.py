"""The bridge from the canvas to the engine (#207, decision 65):
`gui/patch_bridge.py`, `poly.PolyGraph.outputs()`, and
`patch_graph.PatchGraph.judge()` once it is delegating.

No Qt and no audio device. The bridge is deliberately shaped so that
everything it decides -- which module a type key becomes, which side of Mix
it is on, where the sound comes out, what a refusal says -- is answerable
from plain `NodeSpec`s and a stub engine, which is this repo's "pure logic
unit-tested, real I/O smoke-tested" rule applied to a file that sits
between two subsystems and belongs to neither.
"""

import gc
import tracemalloc

import numpy as np
import pytest

pytest.importorskip("scipy.signal")

from notecolor.audio.graph.modules.delay import Delay
from notecolor.audio.graph.modules.envelope import AmpEnvelope
from notecolor.audio.graph.modules.oscillator import WavetableOscillator
from notecolor.audio.graph.modules.passthrough import Passthrough
from notecolor.audio.graph.contract import Activation, ProcessContext
from notecolor.gui import patch_bridge as pb
from notecolor.gui import patch_graph as pg

SAMPLE_RATE = 44100
BLOCK = 512


class StubEngine:
    """Enough of a `SoundEngine` for the bridge: a rate, a block size, and
    somewhere to put the graph. Deliberately not a mock -- the assertions
    below are about what was installed, which is a plain attribute."""

    def __init__(self):
        self.sample_rate = SAMPLE_RATE
        self.block_size = BLOCK
        self.graph = None

    def set_graph(self, graph, activate=True):
        self.graph = graph


def _specs(*extra):
    """The default patch the Synth View opens with -- Osc, Filter, Amp Env,
    Mix -- plus whatever a test adds."""
    base = [
        pg.NodeSpec("mix", "MIX", side=pg.SIDE_BOUNDARY, is_mix=True),
        pg.NodeSpec("osc1", "OSC 1", can_in=False),
        pg.NodeSpec("filter", "FILTER"),
        pg.NodeSpec("amp_env", "AMP ENV"),
    ]
    return base + list(extra)


def _delay(node_id="delay", title="Delay"):
    return pg.NodeSpec(node_id, title, side=pg.SIDE_MONO, is_delay=True)


def _chorus():
    return pg.NodeSpec("chorus", "Chorus", side=pg.SIDE_MONO)


def _cables(*pairs):
    return [pg.Cable(source, dest) for source, dest in pairs]


DEFAULT_CHAIN = (("osc1", "filter"), ("filter", "amp_env"), ("amp_env", "mix"))


def _bridge(specs, cables, parameters=None, engine=None):
    engine = engine or StubEngine()
    bridge = pb.PatchBridge(lambda: engine)
    if parameters:
        bridge.set_parameters(parameters)
    bridge.rebuild(specs, cables)
    return bridge, engine


# -- type keys become modules -----------------------------------------------

def test_each_canvas_module_becomes_the_engine_module_it_looks_like():
    kinds = {
        "osc1": WavetableOscillator, "osc2": WavetableOscillator,
        "filter": type(pb.module_for(pg.NodeSpec("filter", "FILTER"))),
        "amp_env": AmpEnvelope, "delay": Delay,
    }
    for type_key, expected in kinds.items():
        spec = pg.NodeSpec(type_key, type_key.upper())
        assert isinstance(pb.module_for(spec), expected)


def test_a_module_the_engine_cannot_play_becomes_a_wire_and_says_so():
    """Chorus has no graph module yet. Leaving it out would make the cable
    into it go nowhere, so it is built as a passthrough -- and named."""
    graph, notices = pb.build_graph(_specs(_chorus()), [])
    assert isinstance(graph.node("chorus").module, Passthrough)
    assert notices == ["Chorus passes sound through unchanged"]


def test_a_modulation_source_is_not_in_the_engine_graph_at_all():
    """An LFO's cable goes onto a knob, never into a socket, so it is not
    an end of any sound cable and leaving it out removes nothing from the
    signal path. It is still named, because its knobs do nothing yet."""
    specs = _specs(pg.NodeSpec("lfo", "LFO", can_in=False, out_kind=pg.KIND_MOD))
    graph, notices = pb.build_graph(specs, [])
    assert graph.node("lfo") is None
    assert "LFO turns nothing yet" in notices


def test_the_canvas_decides_which_side_of_mix_a_module_is_on():
    """A Filter declares `POLY_EITHER`; the canvas has already put it on
    the per-note side. Without that handed down, an unpatched Filter would
    fall back to once-only and the very first default cable -- Osc into
    Filter -- would be refused."""
    graph, _ = pb.build_graph(_specs(_delay()), [])
    assert graph.node("filter").descriptor.poly == "per_note"
    assert graph.node("delay").descriptor.poly == "once"


def test_a_refusal_calls_a_module_what_the_window_calls_it():
    bridge, _ = _bridge(_specs(_chorus()), _cables(*DEFAULT_CHAIN))
    verdict = bridge.judge("osc1", "chorus")
    assert not verdict.ok
    assert "OSC 1" in verdict.reason and "Chorus" in verdict.reason


# -- where the sound comes out ----------------------------------------------

def test_with_nothing_after_mix_the_mix_node_is_the_output():
    bridge, _ = _bridge(_specs(), _cables(*DEFAULT_CHAIN))
    assert bridge.playing.outputs() == [("mix", "out")]


def test_the_end_of_the_chain_after_mix_is_the_output():
    bridge, _ = _bridge(_specs(_delay(), _chorus()),
                        _cables(*DEFAULT_CHAIN, ("mix", "delay"), ("delay", "chorus")))
    assert bridge.playing.outputs() == [("chorus", "out")]


def test_two_unpatched_ends_after_mix_are_both_outputs():
    """Anything you do not patch onward goes to the speakers -- all of it,
    summed, not just whichever the engine happened to visit last."""
    bridge, _ = _bridge(_specs(_delay(), _chorus()),
                        _cables(*DEFAULT_CHAIN, ("mix", "delay"), ("mix", "chorus")))
    assert bridge.playing.outputs() == [("delay", "out"), ("chorus", "out")]


def test_a_module_nobody_patched_is_not_an_output():
    """An effect sitting unwired on the canvas is silent, not a second
    voice of whatever it was last connected to."""
    bridge, _ = _bridge(_specs(_chorus()), _cables(*DEFAULT_CHAIN))
    assert bridge.playing.outputs() == [("mix", "out")]


def test_a_loop_after_mix_still_has_an_output():
    """Every node on `MIX -> Delay -> Bypass -> Delay` feeds something, so
    "no outgoing cable" finds no output at all and the patch is silent --
    the one failure this rule exists to prevent. A loop has no end, so
    every node on it counts as one."""
    bridge, _ = _bridge(_specs(_delay(), _chorus()),
                        _cables(*DEFAULT_CHAIN, ("mix", "delay"),
                                ("delay", "chorus"), ("chorus", "delay")))
    assert bridge.playing.outputs() == [("delay", "out"), ("chorus", "out")]


# -- it makes a sound --------------------------------------------------------

def _render(bridge, blocks, pitch=60):
    bridge.note_on(pitch)
    peak = 0.0
    for _ in range(blocks):
        peak = max(peak, float(np.abs(bridge.playing.output_block(BLOCK)).max()))
    return peak


def test_the_default_chain_makes_a_sound():
    bridge, _ = _bridge(_specs(), _cables(*DEFAULT_CHAIN))
    assert _render(bridge, 8) > 0.1


def test_taking_the_envelope_out_of_the_chain_changes_what_is_heard():
    """Acceptance 2, as far as a test can carry it: the same modules, a
    different cable, a different signal. The envelope's attack means the
    first block through it is quiet; straight from the filter it is not."""
    with_env, _ = _bridge(_specs(), _cables(*DEFAULT_CHAIN))
    without, _ = _bridge(_specs(), _cables(("osc1", "filter"), ("filter", "mix")))
    with_env.note_on(60)
    without.note_on(60)
    first_with = float(np.abs(with_env.playing.output_block(BLOCK)).max())
    first_without = float(np.abs(without.playing.output_block(BLOCK)).max())
    assert first_without > first_with


def test_a_note_off_with_no_envelope_patched_keeps_sounding():
    """A modular with no envelope drones -- `poly.py`'s stated behaviour,
    reached through the bridge to prove the bridge does not add one."""
    bridge, _ = _bridge(_specs(), _cables(("osc1", "mix")))
    bridge.note_on(60)
    for _ in range(4):
        bridge.playing.output_block(BLOCK)
    bridge.note_off(60)
    after = max(float(np.abs(bridge.playing.output_block(BLOCK)).max())
                for _ in range(4))
    assert after > 0.1


def test_a_loop_through_the_delay_rings_on_after_the_key_is_up():
    """Acceptance 3's shape: the sound is still there long after note-off,
    and it is finite."""
    bridge, _ = _bridge(
        _specs(_delay(), _chorus()),
        _cables(*DEFAULT_CHAIN, ("mix", "delay"), ("delay", "chorus"),
                ("chorus", "delay")),
        parameters={"delay": {"time": 0.05, "mix": 0.5, "feedback": 0.0}})
    bridge.note_on(60)
    for _ in range(20):
        bridge.playing.output_block(BLOCK)
    bridge.note_off(60)
    for _ in range(40):
        bridge.playing.output_block(BLOCK)
    tail = max(float(np.abs(bridge.playing.output_block(BLOCK)).max())
               for _ in range(40))
    assert 0.01 < tail < 1e6
    assert np.isfinite(bridge.playing.output_block(BLOCK)).all()


# -- knobs -------------------------------------------------------------------

def test_one_knob_edit_reaches_all_sixteen_voices():
    bridge, _ = _bridge(_specs(), _cables(*DEFAULT_CHAIN))
    assert bridge.set_parameter("filter", "cutoff", 400.0)
    for voice in bridge.playing.voices:
        assert voice.graph.node("filter").module.params.get("cutoff") == 400.0


def test_a_knob_with_no_engine_parameter_is_ignored_rather_than_raising():
    """`filter.env_amount` is a real knob with nothing behind it until the
    modulation layer lands. A knob that raises is worse for the person
    turning it than a knob that waits."""
    bridge, _ = _bridge(_specs(), _cables(*DEFAULT_CHAIN))
    assert bridge.set_parameter("filter", "env_amount", 0.5) is False


def test_a_choice_knob_arrives_as_the_position_the_contract_stores():
    bridge, _ = _bridge(_specs(), _cables(*DEFAULT_CHAIN))
    bridge.set_parameter("filter", "type", "bp")
    assert bridge.playing.voices[0].graph.node("filter").module.params.get("type") == 2


def test_a_rebuild_starts_the_new_graph_where_the_knobs_are():
    bridge, _ = _bridge(_specs(), _cables(*DEFAULT_CHAIN))
    bridge.set_parameter("filter", "cutoff", 800.0)
    bridge.rebuild(_specs(), _cables(*DEFAULT_CHAIN))
    assert bridge.playing.voices[0].graph.node("filter").module.params.get("cutoff") == 800.0


# -- what a canvas node can take in -----------------------------------------

def test_an_oscillator_offers_no_hole_to_put_sound_into():
    """Otherwise the canvas draws a jack the engine has no port for, and a
    cable dropped on it is refused by naming a port nobody has seen."""
    assert pb.takes_sound_in("osc1") is False
    assert pb.takes_sound_in("filter") is True
    assert pb.takes_sound_in("chorus") is True   # it becomes a wire


# -- installing, swapping and failing ---------------------------------------

def test_the_graph_is_handed_over_by_one_assignment_after_it_is_built():
    bridge, engine = _bridge(_specs(), _cables(*DEFAULT_CHAIN))
    assert engine.graph is bridge.playing
    first = engine.graph
    bridge.rebuild(_specs(), _cables(("osc1", "mix")))
    assert engine.graph is not first


def test_a_patch_with_no_mix_node_stops_the_sound_and_says_why():
    bridge, engine = _bridge([pg.NodeSpec("osc1", "OSC 1", can_in=False)], [])
    assert bridge.playing is None
    assert engine.graph is None
    assert "Mix" in bridge.error


def test_a_view_with_no_audio_device_still_builds_and_still_judges():
    """The Synth View has always been usable with no sound card, and the
    rules must not become the first thing that needs one."""
    bridge = pb.PatchBridge(lambda: None)
    assert bridge.rebuild(_specs(), _cables(*DEFAULT_CHAIN)) is False
    assert bridge.active is False
    assert bridge.judge("osc1", "mix").ok


# -- the delegation (decision 65 §4) ----------------------------------------

def _delegating_graph():
    """A `PatchGraph` with the engine behind it, wired the way
    `synth_view` wires them."""
    specs = _specs(_delay(), _chorus())
    canvas = pg.PatchGraph()
    for spec in specs:
        canvas.add_node(spec)
    bridge = pb.PatchBridge(lambda: StubEngine())
    canvas.engine_judge = bridge.judge

    def resync():
        bridge.rebuild(canvas.nodes(), canvas.cables)

    resync()
    return canvas, resync


def test_the_canvas_asks_the_engine_and_the_engine_refuses_the_boundary():
    canvas, _ = _delegating_graph()
    verdict = canvas.judge("osc1", pg.Target("socket", "chorus"))
    assert not verdict.ok
    assert "MIX" in verdict.reason


def test_the_engine_names_the_whole_loop_it_refuses():
    canvas, resync = _delegating_graph()
    canvas.add_node(pg.NodeSpec("second", "SECOND", side=pg.SIDE_MONO))
    canvas.connect("mix", pg.Target("socket", "chorus"))
    canvas.connect("chorus", pg.Target("socket", "second"))
    resync()
    verdict = canvas.judge("second", pg.Target("socket", "chorus"))
    assert not verdict.ok
    assert "Chorus → SECOND → Chorus" in verdict.reason


def test_the_engine_allows_the_loop_that_goes_through_the_delay():
    canvas, resync = _delegating_graph()
    canvas.connect("mix", pg.Target("socket", "delay"))
    canvas.connect("delay", pg.Target("socket", "chorus"))
    resync()
    assert canvas.judge("chorus", pg.Target("socket", "delay")).ok


def test_the_engine_refuses_a_loop_that_runs_back_through_mix():
    """A rule the canvas's stand-in never had: a node on both sides of the
    boundary would have to be sixteen copies and one copy at once."""
    canvas, resync = _delegating_graph()
    canvas.connect("amp_env", pg.Target("socket", "mix"))
    canvas.connect("mix", pg.Target("socket", "delay"))
    resync()
    verdict = canvas.judge("delay", pg.Target("socket", "amp_env"))
    assert not verdict.ok
    assert "MIX" in verdict.reason


def test_a_duplicate_keeps_the_canvas_sentence_not_the_engine_s():
    """The engine's names a port ("into In on FILTER"); the canvas draws
    unlabelled holes, so a port name there names something invisible."""
    canvas, resync = _delegating_graph()
    canvas.connect("osc1", pg.Target("socket", "filter"))
    resync()
    verdict = canvas.judge("osc1", pg.Target("socket", "filter"))
    assert not verdict.ok
    assert verdict.reason == "OSC 1 is already patched into FILTER."


def test_modulation_is_still_judged_by_the_canvas_because_the_engine_has_none():
    canvas, _ = _delegating_graph()
    canvas.add_node(pg.NodeSpec("lfo", "LFO", can_in=False, out_kind=pg.KIND_MOD))
    assert canvas.judge("lfo", pg.Target("knob", "filter", "Cutoff")).ok
    verdict = canvas.judge("lfo", pg.Target("socket", "filter"))
    assert not verdict.ok
    assert "Drop it on the knob" in verdict.reason


def test_with_no_engine_attached_the_canvas_answers_everything_itself():
    """The stand-in stays, and stays correct: `patch_graph` is still
    usable, and still tested, on its own."""
    canvas = pg.PatchGraph()
    canvas.add_node(pg.NodeSpec("osc1", "OSC 1"))
    canvas.add_node(pg.NodeSpec("chorus", "CHORUS", side=pg.SIDE_MONO))
    assert canvas.engine_judge is None
    verdict = canvas.judge("osc1", pg.Target("socket", "chorus"))
    assert not verdict.ok
    assert "MIX" in verdict.reason


# -- the audio thread allocates nothing --------------------------------------

def test_reading_the_patch_output_allocates_nothing_that_scales_with_the_block():
    """The method `tests/test_synth_short_delay.py` uses: peak traced
    memory at two block sizes. `output_block()` -- run the voices, sum them
    at Mix, run the once-only side, sum the outputs -- is the whole of what
    #207 adds to the callback, and a per-block temporary anywhere in it
    would scale with the block and show up as the difference.

    **The Filter is deliberately not in this patch.** It allocates one
    block per call, because `scipy.signal.lfilter` returns a new array;
    that predates this ticket (decision 62's module, and
    `synth_engine.py`'s filter before it) and is flagged on #207 rather
    than fixed here. Including it would make this test measure that instead
    of measuring anything about the bridge.
    """
    def peak(frames):
        engine = StubEngine()
        engine.block_size = 2048
        specs = [
            pg.NodeSpec("mix", "MIX", side=pg.SIDE_BOUNDARY, is_mix=True),
            pg.NodeSpec("osc1", "OSC 1", can_in=False),
            pg.NodeSpec("amp_env", "AMP ENV"),
            _delay(), _chorus(),
        ]
        bridge, _ = _bridge(specs,
                            _cables(("osc1", "amp_env"), ("amp_env", "mix"),
                                    ("mix", "delay"), ("mix", "chorus")),
                            engine=engine)
        poly = bridge.playing
        bridge.note_on(60)
        for _ in range(4):
            poly.output_block(frames)
        gc.collect()
        tracemalloc.start()
        for _ in range(4):
            poly.output_block(frames)
        tracemalloc.reset_peak()
        base = tracemalloc.get_traced_memory()[0]
        for _ in range(32):
            poly.output_block(frames)
        top = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        return top - base

    assert peak(2048) - peak(64) < 16 * 1024


def test_the_control_shows_the_allocation_check_can_fail():
    """Without this, a check that measures nothing passes for the wrong
    reason. A module that builds one array per block is the thing the test
    above is looking for, and it has to be found."""
    class Leaky(Passthrough):
        def process(self, ctx):
            ctx.outputs[0][:ctx.frames] = np.zeros(ctx.frames) + ctx.inputs[0][:ctx.frames]

    def peak(frames):
        module = Leaky()
        module.activate(Activation(SAMPLE_RATE, 2048))
        ctx = ProcessContext(frames=frames, sample_rate=SAMPLE_RATE,
                             inputs=(np.zeros(2048),), outputs=(np.zeros(2048),),
                             params=module.params.values)
        for _ in range(4):
            module.process(ctx)
        gc.collect()
        tracemalloc.start()
        for _ in range(4):
            module.process(ctx)
        tracemalloc.reset_peak()
        base = tracemalloc.get_traced_memory()[0]
        for _ in range(32):
            module.process(ctx)
        top = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        return top - base

    assert peak(2048) - peak(64) >= 16 * 1024


def test_a_wire_copies_rather_than_handing_its_own_input_out():
    """Two passthroughs fed from one buffer must not end up sharing it --
    the host binds an output buffer straight through as the next module's
    input, so a rebind rather than a copy would corrupt both."""
    module = Passthrough()
    module.activate(Activation(SAMPLE_RATE, BLOCK))
    source = np.arange(BLOCK, dtype=np.float64)
    out = np.zeros(BLOCK)
    ctx = ProcessContext(frames=BLOCK, sample_rate=SAMPLE_RATE,
                         inputs=(source,), outputs=(out,),
                         params=module.params.values)
    module.process(ctx)
    assert out is not source
    assert np.array_equal(out, source)
