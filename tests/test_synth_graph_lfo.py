"""The modulation layer (#208, decision 56 §5's settlement): the LFO module,
the second routing table's refusal rules, and the end-to-end claim stage 1
exists to prove -- an LFO can audibly wobble a filter's cutoff, per-note and
global.

Headless and silent, like every other test in this stream: no audio device
opens, and none is needed, because every claim below is a claim about
arrays. What is worth testing, as opposed to plumbing:

- the LFO satisfies the contract and carries both jacks (the settlement's
  softening of decision 56 §5), and its per-note/global switch is baked
  into construction, not a runtime knob (`new_instance()` must carry it);
- retrigger really does gate whether a note-on restarts the phase;
- the routing table's refusals read the way #203's `Verdict`s do -- a
  sentence, never a bare code -- for every one of the settlement's rules:
  `modulatable=False`, wrong port kind, self-modulation, duplicates, and
  the poly-boundary direction (§3: global -> per-note legal, per-note ->
  once-only refused);
- depth sums from multiple sources and clamps once, at the destination;
- a parameter nothing is patched into never materialises a buffer -- the
  whole reason for the scalar-or-buffer split (§2) -- proved the same way
  `test_synth_graph_level.py` proves it for a module with no departure to
  excuse;
- and the "done when" itself: a per-note LFO and a global LFO can each
  reach a filter's cutoff and move it enough to hear, measured as an RMS
  swing rather than eyeballed.
"""

import gc
import tracemalloc

import numpy as np
import pytest

from notecolor.audio.graph import contract
from notecolor.audio.graph.contract import Activation, NoteContext, ProcessContext
from notecolor.audio.graph.graph import (
    REFUSE_DUPLICATE, REFUSE_NOT_MODULATABLE, REFUSE_POLY, REFUSE_SELF, REFUSE_TYPE,
    ModuleGraph,
)
from notecolor.audio.graph.poly import MixModule, PolyGraph
from notecolor.audio.graph.modules.filter import StateVariableFilter
from notecolor.audio.graph.modules.lfo import Lfo
from notecolor.audio.graph.modules.oscillator import WavetableOscillator

SAMPLE_RATE = 44100.0
BLOCK = 512


def host(module, frames=BLOCK, sample_rate=SAMPLE_RATE, note=None):
    """Same helper every other module test file repeats rather than
    imports, so none of their conventions can drift into one another's."""
    module.activate(Activation(sample_rate, frames))
    ports = module.ports()
    inputs = tuple(np.zeros(frames) for p in ports if p.direction == contract.DIRECTION_IN)
    outputs = tuple(np.zeros(frames) for p in ports if p.direction == contract.DIRECTION_OUT)
    return ProcessContext(
        frames=frames, sample_rate=sample_rate, inputs=inputs, outputs=outputs,
        params=module.params.values, note=note,
    )


# -- the module itself --------------------------------------------------


def test_satisfies_the_contract_with_both_jacks():
    module = Lfo()
    assert contract.validate(module) is module
    kinds = {p.port_id: p.kind for p in module.ports()}
    assert kinds["mod"] == contract.PORT_MOD
    assert kinds["out"] == contract.PORT_AUDIO


def test_default_mode_is_per_note():
    assert Lfo().descriptor().poly == contract.POLY_PER_NOTE


def test_global_mode_is_once_only():
    assert Lfo("global").descriptor().poly == contract.POLY_ONCE


def test_new_instance_carries_the_mode_a_clone_needs():
    """The pattern `WavetableOscillator.new_instance()` already uses for its
    waveform: `mode` is not a parameter, so the default reconstruction would
    silently hand a voice clone the wrong poly mode."""
    original = Lfo("global")
    clone = original.new_instance()
    assert clone.mode == "global"
    assert clone.descriptor().poly == contract.POLY_ONCE


def test_unknown_mode_is_a_contract_error():
    with pytest.raises(contract.ContractError):
        Lfo("stereo")


def test_mod_and_audio_outputs_agree():
    """Both jacks carry the same signal (the module docstring's "exactly the
    same signal" claim) -- the split is for the cable, not the content."""
    module = Lfo()
    ctx = host(module)
    module.params.set("rate", 3.0)
    module.process(ctx)
    mod_out = ctx.outputs[module.port_index("mod", contract.DIRECTION_OUT)]
    audio_out = ctx.outputs[module.port_index("out", contract.DIRECTION_OUT)]
    assert np.array_equal(mod_out, audio_out)


@pytest.mark.parametrize("shape_index,shape_name", list(enumerate(
    ("sine", "triangle", "square", "saw"))))
def test_every_shape_is_bipolar_and_starts_where_it_should(shape_index, shape_name):
    """Matches `synth_engine.lfo_shape()`'s own convention: sine and
    triangle start at (or near) zero, saw and square start at an extreme."""
    module = Lfo()
    ctx = host(module)
    module.params.set("shape", shape_index)
    module.params.set("rate", 1.0)     # one full cycle across ~44100 frames
    module.process(ctx)
    out = ctx.outputs[module.port_index("mod", contract.DIRECTION_OUT)]
    assert np.all(out >= -1.0 - 1e-9) and np.all(out <= 1.0 + 1e-9)
    if shape_name in ("sine", "triangle"):
        assert abs(out[0]) < 0.05
    elif shape_name == "saw":
        assert out[0] == pytest.approx(-1.0, abs=0.05)
    else:  # square
        assert out[0] == pytest.approx(1.0, abs=1e-9)


def test_retrigger_resets_the_phase_on_reset():
    module = Lfo()
    ctx = host(module)
    module.params.set("rate", 2.0)
    module.params.set("retrigger", 1)
    module.process(ctx)   # advances the phase away from 0
    assert module._phase != 0.0
    module.reset()
    assert module._phase == 0.0


def test_free_running_ignores_reset():
    """The settlement's own open question, answered: a per-note LFO that
    does not restart on each note is a real musical option, so it gets a
    knob rather than being unreachable."""
    module = Lfo()
    ctx = host(module)
    module.params.set("rate", 2.0)
    module.params.set("retrigger", 0)
    module.process(ctx)
    phase_before = module._phase
    assert phase_before != 0.0
    module.reset()
    assert module._phase == phase_before


def test_process_allocates_nothing():
    """Same measurement `test_synth_graph_level.py` uses: no departure to
    excuse here either."""
    small = Lfo()
    small.activate(Activation(SAMPLE_RATE, 64))
    large = Lfo()
    large.activate(Activation(SAMPLE_RATE, 2048))

    def peak_bytes(module, frames):
        ctx = ProcessContext(
            frames=frames, sample_rate=SAMPLE_RATE,
            outputs=tuple(np.zeros(2048) for _ in module.ports()
                          if _.direction == contract.DIRECTION_OUT),
            params=module.params.values,
        )
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
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        return peak - base

    grew = peak_bytes(large, 2048) - peak_bytes(small, 64)
    assert grew < 4096


# -- the routing table's refusals (ModuleGraph.judge_modulation) --------


def _graph_with_lfo_and_filter():
    g = ModuleGraph()
    g.add("lfo", Lfo())
    g.add("filter", StateVariableFilter())
    return g


def test_a_mod_cable_lands_on_the_mod_port_not_the_audio_one():
    g = _graph_with_lfo_and_filter()
    verdict = g.judge_modulation("lfo", "out", "filter", "cutoff")
    assert not verdict.ok
    assert verdict.code == REFUSE_TYPE
    assert verdict.reason


def test_a_non_modulatable_knob_is_refused_with_a_sentence():
    g = _graph_with_lfo_and_filter()
    verdict = g.judge_modulation("lfo", "mod", "filter", "type")
    assert not verdict.ok
    assert verdict.code == REFUSE_NOT_MODULATABLE
    assert "Type" in verdict.reason or "type" in verdict.reason


def test_a_modulatable_knob_accepts_the_cable():
    g = _graph_with_lfo_and_filter()
    assert g.judge_modulation("lfo", "mod", "filter", "cutoff").ok


def test_self_modulation_is_refused():
    g = ModuleGraph()
    g.add("lfo", Lfo())
    verdict = g.judge_modulation("lfo", "mod", "lfo", "rate")
    assert not verdict.ok
    assert verdict.code == REFUSE_SELF


def test_a_duplicate_route_is_refused():
    g = _graph_with_lfo_and_filter()
    assert g.connect_modulation("lfo", "mod", "filter", "cutoff").ok
    verdict = g.judge_modulation("lfo", "mod", "filter", "cutoff")
    assert not verdict.ok
    assert verdict.code == REFUSE_DUPLICATE


def test_a_missing_knob_is_refused():
    g = _graph_with_lfo_and_filter()
    verdict = g.judge_modulation("lfo", "mod", "filter", "nope")
    assert not verdict.ok


def test_set_modulation_depth_does_not_touch_the_graph_revision():
    """The ring's write path (decision 56 §5's settlement §4): depth is
    edited without a recompile, unlike every structural edit `revision`
    exists to mark."""
    g = _graph_with_lfo_and_filter()
    g.connect_modulation("lfo", "mod", "filter", "cutoff", depth=0.5)
    before = g.revision
    assert g.set_modulation_depth("lfo", "mod", "filter", "cutoff", -0.3)
    assert g.revision == before
    assert g.mod_depths[g.mod_connections[0].depth_index] == pytest.approx(-0.3)


def test_depth_is_clamped_bipolar():
    g = _graph_with_lfo_and_filter()
    g.connect_modulation("lfo", "mod", "filter", "cutoff", depth=5.0)
    assert g.mod_depths[g.mod_connections[0].depth_index] == pytest.approx(1.0)


# -- the poly-boundary rule (PolyGraph.judge_modulation) -----------------


def _poly_patch(lfo_mode):
    g = ModuleGraph()
    g.add("osc", WavetableOscillator("saw"))
    g.add("lfo", Lfo(lfo_mode))
    g.add("filter", StateVariableFilter())
    g.add("mix", MixModule())
    assert g.connect("osc", "out", "filter", "in").ok
    assert g.connect("filter", "out", "mix", "in").ok
    return g


def test_per_note_source_into_a_once_only_knob_is_refused():
    """Decision 56 §5's settlement §3, the direction with no Mix node to
    absorb it: sixteen voices each proposing a value for one knob."""
    g = _poly_patch("per_note")
    # Filter is cabled left of Mix here, so it is per-note -- move the LFO
    # to target something once-only instead: reuse Mix's own knob-free
    # shape by adding a Level-like once-only destination is unnecessary;
    # the per-note filter itself, forced onto the once-only side by a
    # second, once-only filter, is simplest.
    g.add("master", StateVariableFilter())
    assert g.connect("mix", "out", "master", "in").ok
    poly = PolyGraph(g).activate(Activation(SAMPLE_RATE, BLOCK))
    verdict = poly.judge_modulation("lfo", "mod", "master", "cutoff")
    assert not verdict.ok
    assert verdict.code == REFUSE_POLY
    assert verdict.reason


def test_global_source_into_a_per_note_knob_is_legal():
    g = _poly_patch("global")
    poly = PolyGraph(g).activate(Activation(SAMPLE_RATE, BLOCK))
    assert poly.judge_modulation("lfo", "mod", "filter", "cutoff").ok


# -- the scalar-or-buffer split: nothing patched, nothing materialised ---


def test_an_unmodulated_parameter_never_goes_mod_active():
    g = _graph_with_lfo_and_filter()
    g.activate(Activation(SAMPLE_RATE, BLOCK))
    compiled = g.compile()
    compiled.process(BLOCK)
    filter_module = g.node("filter").module
    assert not filter_module.params.mod_active[filter_module.params.index("cutoff")]


def test_process_allocates_nothing_extra_with_an_unmodulated_lfo_in_the_patch():
    """The whole point of the scalar-or-buffer split (§2): a patch that has
    an LFO in it but does not patch it anywhere pays nothing extra for
    that LFO's presence, at the graph level."""
    def build(frames):
        g = _graph_with_lfo_and_filter()
        g.activate(Activation(SAMPLE_RATE, frames))
        return g.compile()

    def peak_bytes(compiled, frames):
        note = NoteContext(frequency=220.0)
        for _ in range(4):
            compiled.process(frames, note=note)
        gc.collect()
        tracemalloc.start()
        for _ in range(4):
            compiled.process(frames, note=note)
        tracemalloc.reset_peak()
        base = tracemalloc.get_traced_memory()[0]
        for _ in range(32):
            compiled.process(frames, note=note)
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        return peak - base

    small = build(64)
    large = build(2048)
    grew = peak_bytes(large, 2048) - peak_bytes(small, 64)
    # The filter's own `lfilter` departure is already measured and bounded
    # elsewhere (`test_synth_graph_voice.py`, one block-sized float64 array
    # a block: (2048-64)*8 bytes here); this asserts the *modulation layer*
    # adds nothing on top of it when nothing is patched into it, with the
    # same `tracemalloc`-bookkeeping slack those tests give themselves.
    assert grew < (2048 - 64) * 8 + 4096


# -- depth: multiple sources sum, then clamp once ------------------------


def test_two_sources_sum_then_clamp_once_at_the_destination():
    g = ModuleGraph()
    g.add("lfo1", Lfo())
    g.add("lfo2", Lfo())
    g.add("filter", StateVariableFilter())
    assert g.connect_modulation("lfo1", "mod", "filter", "cutoff", depth=1.0).ok
    assert g.connect_modulation("lfo2", "mod", "filter", "cutoff", depth=1.0).ok
    g.activate(Activation(SAMPLE_RATE, BLOCK))
    # Force both LFOs to their positive extreme (square, phase 0) so the
    # combined contribution unambiguously exceeds the range and must clamp.
    g.node("lfo1").module.params.set_immediate("shape", 2)
    g.node("lfo2").module.params.set_immediate("shape", 2)
    g.node("lfo1").module.params.set_immediate("rate", 0.0001)
    g.node("lfo2").module.params.set_immediate("rate", 0.0001)
    compiled = g.compile()
    compiled.process(BLOCK)
    filter_module = g.node("filter").module
    idx = filter_module.params.index("cutoff")
    buf = filter_module.params.buffers[idx]
    spec_max = filter_module.parameters()[idx].maximum
    assert np.max(buf[:BLOCK]) <= spec_max + 1e-6
    assert np.max(buf[:BLOCK]) == pytest.approx(spec_max, rel=1e-6)


# -- the "done when": an LFO audibly wobbles a filter's cutoff -----------


def _rms_per_block(compiled, node_id, blocks, note, frames=BLOCK):
    levels = []
    for _ in range(blocks):
        compiled.process(frames, note=note)
        levels.append(float(np.sqrt(np.mean(compiled.buffer(node_id)[:frames] ** 2))))
    return levels


def test_a_per_note_lfo_audibly_wobbles_the_filter_cutoff():
    """Stage 1's "done when", per-note: a saw through a filter whose cutoff
    an LFO sweeps swings in loudness as the cutoff crosses the harmonic
    content -- measured as an RMS swing across a dozen blocks, not eyeballed
    from a plot.
    """
    g = ModuleGraph()
    g.add("osc", WavetableOscillator("saw"))
    g.add("lfo", Lfo("per_note"))
    g.add("filter", StateVariableFilter())
    g.add("mix", MixModule())
    assert g.connect("osc", "out", "filter", "in").ok
    assert g.connect("filter", "out", "mix", "in").ok
    assert g.connect_modulation("lfo", "mod", "filter", "cutoff", depth=0.9).ok

    poly = PolyGraph(g, voices=2).activate(Activation(SAMPLE_RATE, BLOCK))
    poly.set_parameter("osc", "level", 0.9)
    poly.set_parameter("filter", "cutoff", 1500.0)
    poly.set_parameter("filter", "resonance", 0.4)
    poly.set_parameter("lfo", "rate", 6.0)   # a handful of cycles over the test

    poly.note_on(48, frequency=110.0)
    levels = []
    for _ in range(24):
        poly.process(BLOCK)
        levels.append(float(np.sqrt(np.mean(poly.buffer("mix") ** 2))))

    assert max(levels) > 0.0, "the voice must actually be sounding"
    swing = (max(levels) - min(levels)) / max(levels)
    assert swing > 0.15, (
        f"the cutoff sweep should be audible as a real loudness swing, got {swing:.3f}")


def _cross_boundary_patch(voices=2):
    """Global LFO -> per-note filter's cutoff: the one modulation direction
    that crosses the Mix boundary (decision 56 §5's settlement §3). The
    filter sits *left* of Mix (per-note, audio-cabled); the LFO is
    `mode="global"` and therefore once-only regardless of cabling."""
    g = ModuleGraph()
    g.add("osc", WavetableOscillator("saw"))
    g.add("filter", StateVariableFilter())
    g.add("mix", MixModule())
    g.add("lfo", Lfo("global"))
    assert g.connect("osc", "out", "filter", "in").ok
    assert g.connect("filter", "out", "mix", "in").ok
    assert g.connect_modulation("lfo", "mod", "filter", "cutoff", depth=0.9).ok
    poly = PolyGraph(g, voices=voices).activate(Activation(SAMPLE_RATE, BLOCK))
    assert poly.sides()["filter"] == "poly"
    assert poly.sides()["lfo"] == "mono"
    return poly


def test_a_global_lfo_reaches_every_per_note_voice():
    """Stage 1's "done when", global: one LFO reaching every voice's own
    filter cutoff across the Mix boundary -- broadcast, not summed, and
    still an audible sweep in the mixed output."""
    poly = _cross_boundary_patch(voices=2)
    poly.set_parameter("osc", "level", 0.9)
    poly.set_parameter("filter", "cutoff", 1500.0)
    poly.set_parameter("filter", "resonance", 0.4)
    poly.set_parameter("lfo", "rate", 6.0)

    poly.note_on(48, frequency=110.0)
    levels = []
    for _ in range(24):
        poly.process(BLOCK)
        levels.append(float(np.sqrt(np.mean(poly.buffer("mix") ** 2))))

    assert max(levels) > 0.0
    swing = (max(levels) - min(levels)) / max(levels)
    assert swing > 0.15, (
        f"the global LFO should audibly sweep every voice's cutoff, got {swing:.3f}")


def test_a_global_lfo_cannot_reach_a_per_note_knob_faster_than_one_block():
    """The latency `PolyGraph.activate()`'s own comment names, checked
    directly: on the very first block, a voice must still see the
    unmodulated base cutoff, because the global LFO's mono-side output has
    not run yet the first time that voice reads it (voices run before the
    once-only side, every block)."""
    poly = _cross_boundary_patch(voices=1)
    poly.set_parameter("filter", "cutoff", 1500.0)
    poly.note_on(48, frequency=110.0)
    poly.process(BLOCK)

    voice_filter = poly.module("filter", voice=0)
    idx = voice_filter.params.index("cutoff")
    assert np.allclose(voice_filter.params.buffers[idx][:BLOCK], 1500.0, atol=1e-6)

    # And on the *second* block, the (by-then-computed) global signal has
    # reached it -- proving the lag is exactly one block, not "never".
    poly.process(BLOCK)
    assert not np.allclose(voice_filter.params.buffers[idx][:BLOCK], 1500.0, atol=1e-3)
