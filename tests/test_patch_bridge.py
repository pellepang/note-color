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
from notecolor.audio.graph.modules.level import Level
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


def _level(node_id="level", title="Level"):
    return pg.NodeSpec(node_id, title, side=pg.SIDE_MONO)


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
        "amp_env": AmpEnvelope, "delay": Delay, "level": Level,
    }
    for type_key, expected in kinds.items():
        spec = pg.NodeSpec(type_key, type_key.upper())
        assert isinstance(pb.module_for(spec), expected)


def test_a_module_the_engine_cannot_play_becomes_a_wire_and_says_so():
    """Chorus has no graph module yet. Leaving it out would make the cable
    into it go nowhere, so it is built as a passthrough -- and named."""
    graph, notices, node_notices = pb.build_graph(_specs(_chorus()), [])
    assert isinstance(graph.node("chorus").module, Passthrough)
    assert notices == ["Chorus passes sound through unchanged"]
    assert node_notices == {"chorus": ("passthrough", "Chorus passes sound through unchanged")}


def test_a_modulation_source_with_no_engine_module_is_not_in_the_graph_at_all():
    """`filter_env` (#208 stage 2's remaining gap, unlike `lfo`/`mod_env`)
    has no engine module yet: its cable goes onto a knob, never into a
    socket, so it is not an end of any sound cable and leaving it out
    removes nothing from the signal path. It is still named, because its
    knobs do nothing yet."""
    specs = _specs(pg.NodeSpec("filter_env", "FILTER ENV", can_in=False, out_kind=pg.KIND_MOD))
    graph, notices, node_notices = pb.build_graph(specs, [])
    assert graph.node("filter_env") is None
    assert "FILTER ENV turns nothing yet" in notices
    assert node_notices["filter_env"] == ("no_module", "FILTER ENV turns nothing yet")


def test_the_lfo_is_a_real_engine_module_with_both_jacks():
    """#208's deliberate softening: the LFO carries both an audio path
    (`KIND_BOTH`) and a modulation one, so it must be a real engine module
    rather than the "mod source has no module" case above."""
    specs = _specs(pg.NodeSpec("lfo", "LFO", can_in=False, out_kind=pg.KIND_BOTH))
    graph, notices, _ = pb.build_graph(specs, [])
    assert graph.node("lfo") is not None
    assert notices == []


def test_a_mod_only_source_with_a_real_module_still_gets_built():
    """`mod_env` (#208 stage 2, decision 67) has no sound jack at all --
    `KIND_MOD`, not `KIND_BOTH` -- but it does have an engine module, so it
    is built like any other node with a factory rather than treated as
    absent the way `filter_env` still is."""
    specs = _specs(pg.NodeSpec("mod_env", "MOD ENV", can_in=False, out_kind=pg.KIND_MOD))
    graph, notices, _ = pb.build_graph(specs, [])
    assert graph.node("mod_env") is not None
    assert notices == []


def test_the_canvas_decides_which_side_of_mix_a_module_is_on():
    """A Filter declares `POLY_EITHER`; the canvas has already put it on
    the per-note side. Without that handed down, an unpatched Filter would
    fall back to once-only and the very first default cable -- Osc into
    Filter -- would be refused."""
    graph, _, _ = pb.build_graph(_specs(_delay()), [])
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


def test_a_level_in_the_loop_brings_a_hot_round_trip_under_control():
    """#218's whole reason to exist, per decision 65 §8: a unity-gain delay
    (dry + wet) patched back into its own input has loop gain >= 1 as soon
    as `Fdbk` leaves zero, and there was nothing on the once-only side that
    could turn that back down. A Level at 0.5 in the loop should hold a
    sustained tail to a visibly smaller ceiling than the same loop with the
    Level left at unity."""
    def loop_peak(level_gain):
        bridge, _ = _bridge(
            _specs(_delay(), _level()),
            _cables(*DEFAULT_CHAIN, ("mix", "delay"), ("delay", "level"),
                    ("level", "delay")),
            parameters={"delay": {"time": 0.01, "mix": 0.9, "feedback": 0.6},
                        "level": {"level": level_gain}})
        bridge.note_on(60)
        for _ in range(20):
            bridge.playing.output_block(BLOCK)
        bridge.note_off(60)
        return max(float(np.abs(bridge.playing.output_block(BLOCK)).max())
                   for _ in range(60))

    hot = loop_peak(1.0)
    turned_down = loop_peak(0.5)
    assert np.isfinite(hot) and np.isfinite(turned_down)
    assert turned_down < hot


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


def test_most_knob_labels_match_the_engine_parameter_by_name():
    assert pb.param_id_for_label("filter", "Cutoff") == "cutoff"
    assert pb.param_id_for_label("filter", "Reso") == "resonance"
    assert pb.param_id_for_label("osc1", "Fine") == "fine"
    assert pb.param_id_for_label("filter", "EnvAmt") is None  # no engine parameter yet


def test_a_label_that_reads_differently_on_the_canvas_still_resolves():
    """Found by a mod cable onto Delay's Feedback silently doing nothing:
    the canvas abbreviates a few labels differently from the engine's own
    `ParamSpec.name` (`LABEL_ALIASES`)."""
    assert pb.param_id_for_label("osc1", "PW") == "pulse_width"
    assert pb.param_id_for_label("osc2", "PW") == "pulse_width"
    assert pb.param_id_for_label("filter", "KeyTrk") == "key_tracking"
    assert pb.param_id_for_label("delay", "Fdbk") == "feedback"
    assert pb.param_id_for_label("delay", "Damp") == "damping"


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


# -- modulation cables, delegated for real (#208) ----------------------------
#
# `_delegating_graph()` above never builds an "lfo" node into the bridge's
# own engine graph (it is only ever added to the bare canvas), so the tests
# above it prove the *fallback*, not the delegation. These build a real
# `PatchBridge` with an LFO in it, wire `engine_judge_modulation` the way
# `synth_view` does, and exercise `judge_modulation()`'s real wording.

def _lfo():
    return pg.NodeSpec("lfo", "LFO", can_in=False, out_kind=pg.KIND_BOTH)


def _mod_delegating_graph(wired=False):
    """`wired=True` also lays the default Osc -> Filter -> Amp Env -> Mix
    sound chain, for a test that needs something to actually hear."""
    specs = _specs(_lfo())
    canvas = pg.PatchGraph()
    for spec in specs:
        canvas.add_node(spec)
    if wired:
        for source, dest in DEFAULT_CHAIN:
            canvas.connect(source, pg.Target("socket", dest))
    bridge = pb.PatchBridge(lambda: StubEngine())
    canvas.engine_judge = bridge.judge
    canvas.engine_judge_modulation = bridge.judge_modulation

    def resync():
        bridge.rebuild(canvas.nodes(), canvas.cables)

    resync()
    return canvas, bridge, resync


def test_a_real_modulation_cable_is_accepted_by_the_engine():
    canvas, _bridge, _resync = _mod_delegating_graph()
    assert canvas.judge("lfo", pg.Target("knob", "filter", "Cutoff")).ok


def test_the_engine_refuses_modulation_onto_a_non_modulatable_knob():
    """`filter.type` is `modulatable=False` (a filter type has no
    in-between positions) -- `judge_modulation()`'s own wording, used
    verbatim rather than a canvas-invented sentence."""
    canvas, _bridge, _resync = _mod_delegating_graph()
    verdict = canvas.judge("lfo", pg.Target("knob", "filter", "Type"))
    assert not verdict.ok
    assert "does not take modulation" in verdict.reason


def test_a_second_destination_on_the_same_source_is_still_fine():
    canvas, _bridge, resync = _mod_delegating_graph()
    canvas.connect("lfo", pg.Target("knob", "filter", "Cutoff"))
    resync()
    assert canvas.judge("lfo", pg.Target("knob", "filter", "Reso")).ok


def test_the_engine_refuses_the_lfo_modulating_its_own_rate():
    canvas, _bridge, _resync = _mod_delegating_graph()
    verdict = canvas.judge("lfo", pg.Target("knob", "lfo", "Rate"))
    assert not verdict.ok
    assert "cannot modulate its own knob" in verdict.reason


def test_the_engine_keeps_the_canvas_duplicate_sentence_for_modulation_too():
    canvas, _bridge, resync = _mod_delegating_graph()
    canvas.connect("lfo", pg.Target("knob", "filter", "Cutoff"))
    resync()
    verdict = canvas.judge("lfo", pg.Target("knob", "filter", "Cutoff"))
    assert not verdict.ok
    assert verdict.reason == "LFO is already on that knob."


def test_a_connected_modulation_cable_reaches_the_engine_and_is_audible():
    canvas, bridge, resync = _mod_delegating_graph()
    canvas.connect("lfo", pg.Target("knob", "filter", "Cutoff"))
    resync()
    assert bridge.playing is not None
    mc = bridge.playing.graph.mod_connections
    assert len(mc) == 1
    assert (mc[0].source, mc[0].source_port, mc[0].dest, mc[0].param_id) == (
        "lfo", "mod", "filter", "cutoff")


def test_the_lfo_s_audio_jack_can_also_patch_into_a_socket():
    """#208's softening: the same node's Out jack is an ordinary sound
    cable, judged by the ordinary sound-cable rules."""
    canvas, _bridge, _resync = _mod_delegating_graph()
    assert canvas.judge("lfo", pg.Target("socket", "filter")).ok


def test_the_ring_edits_depth_live_with_no_rebuild():
    canvas, bridge, resync = _mod_delegating_graph()
    canvas.connect("lfo", pg.Target("knob", "filter", "Cutoff"))
    resync()
    revision_before = bridge.playing.graph.revision
    assert bridge.set_modulation_depth("lfo", "filter", "Cutoff", -0.5)
    assert bridge.playing.graph.revision == revision_before
    index = bridge.playing.graph.mod_connections[0].depth_index
    assert bridge.playing.graph.mod_depths[index] == pytest.approx(-0.5)


def test_the_modulation_cable_is_actually_audible():
    """End to end, the way #208 stage 1's own claim was measured
    (`docs/decisions/66-...md`): an RMS swing across blocks, present with
    the cable patched and absent with an unpatched (but still-running)
    LFO in the same spot -- proving the *cable*, not just the LFO's own
    sound, is what moves the filter."""
    def _swing(depth):
        canvas, bridge, resync = _mod_delegating_graph(wired=True)
        if depth is not None:
            cable = canvas.connect("lfo", pg.Target("knob", "filter", "Cutoff"))
            canvas.set_depth(cable, depth)
        resync()
        bridge.set_parameter("lfo", "rate", 8.0)
        bridge.note_on(60)
        levels = []
        for _ in range(24):
            levels.append(float(np.sqrt(np.mean(bridge.playing.output_block(BLOCK) ** 2))))
        return max(levels) - min(levels)

    modulated = _swing(1.0)
    unpatched = _swing(None)
    assert modulated > unpatched * 3


def test_a_cables_own_depth_survives_a_rebuild():
    """The ring writes both places (`patch_canvas.PatchLayer.
    _update_depth_drag()`): live, via `set_modulation_depth()`, and onto
    the cable's own `depth` field, which is what a later rebuild
    (`build_graph()`) reads back into the fresh graph's `mod_depths`."""
    canvas, bridge, resync = _mod_delegating_graph()
    cable = canvas.connect("lfo", pg.Target("knob", "filter", "Cutoff"))
    resync()
    canvas.set_depth(cable, 0.3)
    resync()
    index = bridge.playing.graph.mod_connections[0].depth_index
    assert bridge.playing.graph.mod_depths[index] == pytest.approx(0.3)


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


# -- MIDI expression (issue #173, decision 72) --------------------------------

def test_pitch_bend_broadcasts_onto_fine_on_every_oscillator_present():
    bridge, _ = _bridge(_specs(pg.NodeSpec("osc2", "OSC 2", can_in=False)),
                        _cables(*DEFAULT_CHAIN))
    bridge.apply_pitch_bend(1.0)   # full bend up
    for node_id in ("osc1", "osc2"):
        for voice in bridge.playing.voices:
            assert voice.graph.node(node_id).module.params.get("fine") == pytest.approx(100.0)


def test_pitch_bend_adds_to_the_users_own_fine_knob_rather_than_replacing_it():
    bridge, _ = _bridge(_specs(), _cables(*DEFAULT_CHAIN))
    bridge.set_parameter("osc1", "fine", 20.0)
    bridge.apply_pitch_bend(0.5)   # half bend up -- +50 cents
    for voice in bridge.playing.voices:
        assert voice.graph.node("osc1").module.params.get("fine") == pytest.approx(70.0)
    # Centering the wheel returns to exactly the knob's own value, not to
    # zero -- a transient gesture composes with, and never clobbers, a
    # deliberate knob edit.
    bridge.apply_pitch_bend(0.0)
    for voice in bridge.playing.voices:
        assert voice.graph.node("osc1").module.params.get("fine") == pytest.approx(20.0)


def test_pitch_bend_does_not_persist_into_a_rebuild():
    """A performance gesture, not a knob position: `_parameters` (what a
    rebuild re-applies) must not remember a bend that has since centered,
    or a rebuild mid-bend would freeze whatever bend was last applied as
    a permanent detune."""
    bridge, _ = _bridge(_specs(), _cables(*DEFAULT_CHAIN))
    bridge.apply_pitch_bend(1.0)
    bridge.rebuild(_specs(), _cables(*DEFAULT_CHAIN))
    for voice in bridge.playing.voices:
        assert voice.graph.node("osc1").module.params.get("fine") == pytest.approx(0.0)


def test_pitch_bend_with_no_graph_playing_is_a_silent_no_op():
    bridge = pb.PatchBridge(lambda: None)
    bridge.rebuild(_specs(), _cables(*DEFAULT_CHAIN))
    bridge.apply_pitch_bend(1.0)   # must not raise


def test_external_cc_feeds_a_midi_cc_module_when_one_is_patched():
    specs = _specs(pg.NodeSpec("midi_cc", "EXTERNAL CC", can_in=False, out_kind=pg.KIND_MOD, side=pg.SIDE_MONO))
    bridge, _ = _bridge(specs, _cables(*DEFAULT_CHAIN))
    bridge.set_external_cc(0.75)
    assert bridge.playing.module("midi_cc")._value == pytest.approx(0.75)


def test_external_cc_with_no_midi_cc_node_is_a_silent_no_op():
    bridge, _ = _bridge(_specs(), _cables(*DEFAULT_CHAIN))
    bridge.set_external_cc(0.5)   # must not raise -- no such node in this patch


def test_external_cc_modulates_a_patched_destination():
    """End-to-end: the mod wheel reaching an oscillator's pitch through the
    same modulation-cable machinery an LFO already uses, exactly as
    `docs/research/midi-hardware-input.md` §4 found it would. The cable is
    part of the patch handed to `rebuild()` (mirroring how a canvas cable
    reaches the engine, `test_a_connected_modulation_cable_reaches_the_
    engine_and_is_audible`'s own convention) rather than connected after
    `activate()`, which has already fixed each voice's `ModRoute`s."""
    specs = _specs(pg.NodeSpec("midi_cc", "EXTERNAL CC", can_in=False, out_kind=pg.KIND_MOD, side=pg.SIDE_MONO))
    cables = list(_cables(*DEFAULT_CHAIN))
    cables.append(pg.Cable(source="midi_cc", knob=("osc1", "Fine"), depth=1.0))
    bridge, _ = _bridge(specs, cables)
    assert len(bridge.playing.graph.mod_connections) == 1
    bridge.set_external_cc(1.0)
    bridge.playing.note_on(60)
    bridge.playing.process(BLOCK)
    voice = bridge.playing.voices[0]
    assert voice.graph.node("osc1").module.params.mod_active[
        voice.graph.node("osc1").module.params.index("fine")]
