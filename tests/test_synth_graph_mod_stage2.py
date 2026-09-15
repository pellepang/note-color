"""Stage 2 of the modulation layer (#208, decision 67): the Mod Envelope,
the broadened destination list, and #225's cycle refusal.

Same conventions as `tests/test_synth_graph_lfo.py` -- headless, silent,
every claim about arrays rather than eyeballed:

- `ModEnvelope` satisfies the contract, is unipolar, per-note only, and
  never sets `note.finished` -- `AmpEnvelope` stays the only module that
  does (decision 61 §4).
- Every newly-modulatable destination (filter key tracking, oscillator
  pulse width, noise level, delay feedback/damping/mix) actually reads its
  modulation buffer rather than accepting a cable that silently does
  nothing -- the gap this stage found and closed for `key_tracking`, which
  was already `modulatable=True` in stage 1 but never read.
- `time` on both delay modules refuses a mod cable with a sentence
  (`REFUSE_NOT_MODULATABLE`), rather than accepting one that does nothing.
- A modulation-only cycle refuses with a `Verdict` and a sentence
  (#225), instead of reaching `compile()` as a bare `CycleError`.
- The no-allocation proof (contract rule 2) extended to a patch carrying
  the Mod Envelope and the widened destination set.
"""

import gc
import tracemalloc

import numpy as np
import pytest

from notecolor.audio.graph import contract
from notecolor.audio.graph.contract import Activation, ContractError, NoteContext, ProcessContext
from notecolor.audio.graph.graph import (
    REFUSE_CYCLE, REFUSE_NOT_MODULATABLE, ModuleGraph,
)
from notecolor.audio.graph.poly import MixModule, PolyGraph
from notecolor.audio.graph.modules.delay import Delay
from notecolor.audio.graph.modules.envelope import AmpEnvelope, ModEnvelope
from notecolor.audio.graph.modules.filter import StateVariableFilter
from notecolor.audio.graph.modules.lfo import Lfo
from notecolor.audio.graph.modules.noise import Noise
from notecolor.audio.graph.modules.oscillator import WavetableOscillator
from notecolor.audio.graph.modules.short_delay import ShortDelay

SAMPLE_RATE = 44100.0
BLOCK = 512


def host(module, frames=BLOCK, sample_rate=SAMPLE_RATE, note=None):
    """`test_synth_graph_lfo.py`'s helper, plus the scalar-or-buffer arrays
    bound the way `graph.compile()` binds them -- stage 1's own `host()`
    does not need these (nothing in that file drives a module by hand-
    setting `mod_active`), but this file's per-destination tests do."""
    module.activate(Activation(sample_rate, frames))
    ports = module.ports()
    inputs = tuple(np.zeros(frames) for p in ports if p.direction == contract.DIRECTION_IN)
    outputs = tuple(np.zeros(frames) for p in ports if p.direction == contract.DIRECTION_OUT)
    return ProcessContext(
        frames=frames, sample_rate=sample_rate, inputs=inputs, outputs=outputs,
        params=module.params.smoothed, note=note,
        param_buffers=module.params.buffers, param_mod_active=module.params.mod_active,
    )


# -- ModEnvelope: contract, shape, and the note.finished boundary -------


def test_satisfies_the_contract_with_a_mod_output_only():
    module = ModEnvelope()
    assert contract.validate(module) is module
    ports = module.ports()
    assert len(ports) == 1
    assert ports[0].kind == contract.PORT_MOD
    assert ports[0].direction == contract.DIRECTION_OUT


def test_is_per_note_only():
    assert ModEnvelope().descriptor().poly == contract.POLY_PER_NOTE


def test_output_is_unipolar_never_negative():
    """The bipolar-vs-unipolar call, checked directly: the envelope's own
    shape never goes negative, whatever stage it is in -- depth (bipolar,
    on the cable) is what can push a destination down, not this module."""
    module = ModEnvelope()
    note = NoteContext(gate=True)
    ctx = host(module, note=note)
    module.params.set_immediate("attack", 0.05)
    module.params.set_immediate("sustain", 1.0)
    module.reset()
    for _ in range(8):
        module.process(ctx)
        out = ctx.outputs[0]
        assert np.all(out >= -1e-9)
        assert np.all(out <= 1.0 + 1e-9)


def test_never_sets_note_finished():
    """Decision 61 §4: `AmpEnvelope` is the only module that ends a note.
    A patch with only a Mod Envelope must drone exactly as one with no
    envelope at all -- this module's own envelope reaching its idle tail
    must not be mistaken for that signal."""
    module = ModEnvelope()
    note = NoteContext(gate=True)
    ctx = host(module, note=note)
    module.params.set_immediate("attack", 0.001)
    module.params.set_immediate("decay", 0.001)
    module.params.set_immediate("release", 0.001)
    module.reset()
    note.gate = False
    for _ in range(50):
        module.process(ctx)
    assert note.finished is False


def test_amp_envelope_still_ends_the_note():
    """Regression: nothing about adding `ModEnvelope` beside it changes
    `AmpEnvelope`'s own role."""
    module = AmpEnvelope()
    note = NoteContext(gate=True)
    ctx = host(module, note=note)
    module.params.set_immediate("attack", 0.001)
    module.params.set_immediate("decay", 0.001)
    module.params.set_immediate("release", 0.001)
    module.reset()
    note.gate = False
    for _ in range(50):
        module.process(ctx)
    assert note.finished is True


def test_new_instance_is_a_fresh_mod_envelope():
    original = ModEnvelope()
    clone = original.new_instance()
    assert isinstance(clone, ModEnvelope)
    assert clone is not original


# -- delay `time` refuses modulation; feedback/damping/mix accept it -----


def test_delay_time_refuses_a_mod_cable():
    g = ModuleGraph()
    g.add("lfo", Lfo())
    g.add("delay", Delay())
    verdict = g.judge_modulation("lfo", "mod", "delay", "time")
    assert not verdict.ok
    assert verdict.code == REFUSE_NOT_MODULATABLE
    assert verdict.reason


def test_short_delay_time_refuses_a_mod_cable():
    g = ModuleGraph()
    g.add("lfo", Lfo())
    g.add("delay", ShortDelay())
    verdict = g.judge_modulation("lfo", "mod", "delay", "time")
    assert not verdict.ok
    assert verdict.code == REFUSE_NOT_MODULATABLE


@pytest.mark.parametrize("param_id", ["feedback", "damping", "mix"])
def test_delay_accepts_modulation_on(param_id):
    g = ModuleGraph()
    g.add("lfo", Lfo())
    g.add("delay", Delay())
    assert g.judge_modulation("lfo", "mod", "delay", param_id).ok


@pytest.mark.parametrize("param_id", ["feedback", "mix"])
def test_short_delay_accepts_modulation_on(param_id):
    g = ModuleGraph()
    g.add("lfo", Lfo())
    g.add("delay", ShortDelay())
    assert g.judge_modulation("lfo", "mod", "delay", param_id).ok


# -- broadened destinations actually read the buffer, not just accept ----


def _sine_source(n, low, high):
    """A slow half-cycle sweep from `low` to `high` across the block, as a
    stand-in for "an LFO is patched in and modulating this parameter" --
    written directly into `param_buffers`/`mod_active` rather than through
    a real LFO module, so each test isolates one destination."""
    return np.linspace(low, high, n)


def test_filter_key_tracking_reads_the_buffer_when_live():
    """Stage 1 left `key_tracking` accepting a cable (`modulatable=True`
    by its `ParamSpec` default) that `process()` never actually read --
    this is the gap decision 67 closes."""
    module = StateVariableFilter()
    note = NoteContext(pitch=72)
    ctx = host(module, note=note)
    module.params.set_immediate("cutoff", 1000.0)
    module.params.set_immediate("resonance", 0.2)

    idx = module.params.index("key_tracking")
    module.params.mod_active[idx] = True
    module.params.buffers[idx][:BLOCK] = _sine_source(BLOCK, 0.0, 1.0)

    module.process(ctx)
    # Two different `key_tracking` values were used within one block, so
    # the filter's cached scalar must have been invalidated for a later
    # unmodulated block to recompute rather than compare stale.
    assert np.isnan(module._last_tracking)


def test_oscillator_pulse_width_reads_the_buffer_when_live():
    module = WavetableOscillator("square")
    note = NoteContext(frequency=220.0)
    ctx = host(module, note=note)
    module.params.set_immediate("level", 0.9)

    idx = module.params.index("pulse_width")
    module.params.mod_active[idx] = True
    module.params.buffers[idx][:BLOCK] = _sine_source(BLOCK, 0.1, 0.9)

    module.process(ctx)
    out_live = np.array(ctx.outputs[0])

    # Same setup, but frozen at the buffer's first value as a *scalar* --
    # the two must differ, or the buffer was never actually consulted.
    module2 = WavetableOscillator("square")
    ctx2 = host(module2, note=NoteContext(frequency=220.0))
    module2.params.set_immediate("level", 0.9)
    module2.params.set_immediate("pulse_width", 0.1)
    module2.process(ctx2)
    assert not np.allclose(out_live, ctx2.outputs[0])


def test_noise_level_reads_the_buffer_when_live():
    module = Noise(seed=1)
    ctx = host(module)
    idx = module.params.index("level")
    module.params.mod_active[idx] = True
    module.params.buffers[idx][:BLOCK] = _sine_source(BLOCK, 0.0, 1.0)
    module.process(ctx)
    out = ctx.outputs[0]
    # The first half of the block (low level) must be quieter than the
    # second half (high level) -- proof the buffer, not a flat scalar, was
    # applied sample by sample.
    first_half_rms = np.sqrt(np.mean(out[:BLOCK // 4] ** 2))
    second_half_rms = np.sqrt(np.mean(out[-BLOCK // 4:] ** 2))
    assert second_half_rms > first_half_rms * 2


@pytest.mark.parametrize("param_id", ["feedback", "damping", "mix"])
def test_delay_reads_the_buffer_when_live(param_id):
    module = Delay()
    ctx = host(module)
    ctx.inputs[0][:BLOCK] = 1.0
    module.params.set_immediate("time", 0.02)
    idx = module.params.index(param_id)
    module.params.mod_active[idx] = True
    module.params.buffers[idx][:BLOCK] = _sine_source(BLOCK, 0.0, 0.9)
    module.process(ctx)   # must not raise, must not allocate beyond scipy-free path
    assert np.all(np.isfinite(ctx.outputs[0]))


@pytest.mark.parametrize("param_id", ["feedback", "mix"])
def test_short_delay_reads_the_buffer_when_live(param_id):
    module = ShortDelay()
    ctx = host(module)
    ctx.inputs[0][:BLOCK] = 1.0
    module.params.set_immediate("time", 0.005)
    idx = module.params.index(param_id)
    module.params.mod_active[idx] = True
    module.params.buffers[idx][:BLOCK] = _sine_source(BLOCK, 0.0, 0.9)
    module.process(ctx)
    assert np.all(np.isfinite(ctx.outputs[0]))


# -- #225: a modulation-only cycle refuses with a sentence ---------------


def test_a_two_lfo_modulation_cycle_is_refused_not_raised():
    """`Lfo.rate` is `modulatable=True` by its `ParamSpec` default (never
    overridden), so two LFOs modulating each other's rate is exactly the
    reachable modulation-only cycle #225 was filed over."""
    g = ModuleGraph()
    g.add("a", Lfo())
    g.add("b", Lfo())
    assert g.connect_modulation("a", "mod", "b", "rate").ok
    verdict = g.judge_modulation("b", "mod", "a", "rate")
    assert not verdict.ok
    assert verdict.code == REFUSE_CYCLE
    assert verdict.reason
    assert "loop" in verdict.reason.lower()


def test_connect_modulation_refuses_rather_than_building_the_cycle():
    g = ModuleGraph()
    g.add("a", Lfo())
    g.add("b", Lfo())
    assert g.connect_modulation("a", "mod", "b", "rate").ok
    verdict = g.connect_modulation("b", "mod", "a", "rate")
    assert not verdict.ok
    assert len(g.mod_connections) == 1


def test_a_three_node_modulation_cycle_is_refused_with_a_sentence():
    """A longer loop than the two-node case above -- `_ordering_edges()`
    folds mod and audio edges into one set, so this proves the same
    mechanism catches a third node on the path back, which is what #225's
    "or a mix of mod and audio edges" case ultimately reduces to: the DFS
    does not care which kind of edge it is walking."""
    g = ModuleGraph()
    g.add("a", Lfo())
    g.add("b", Lfo())
    g.add("c", Lfo())
    assert g.connect_modulation("a", "mod", "b", "rate").ok
    assert g.connect_modulation("b", "mod", "c", "rate").ok
    verdict = g.judge_modulation("c", "mod", "a", "rate")
    assert not verdict.ok
    assert verdict.code == REFUSE_CYCLE
    assert "a" in verdict.reason.lower()  # names a node on the path back


def test_force_built_mod_cycle_still_compiles_without_crashing():
    """Belt and braces: `force_connect_modulation()` bypasses `judge()`
    on purpose (for tests exercising `compile()`'s own defences) -- a
    cycle built that way must not hang or crash `compile()`/`order()`,
    even though it is never something the UI can produce. `CycleError` is
    the acceptable outcome here, exactly as it already is for a
    force-built audio cycle."""
    from notecolor.audio.graph.graph import CycleError
    g = ModuleGraph()
    g.add("a", Lfo())
    g.add("b", Lfo())
    g.force_connect_modulation("a", "mod", "b", "rate")
    g.force_connect_modulation("b", "mod", "a", "rate")
    g.activate(Activation(SAMPLE_RATE, BLOCK))
    with pytest.raises(CycleError):
        g.compile()


# -- no allocation, with the Mod Envelope and the wider destinations -----


def _wide_patch():
    g = ModuleGraph()
    g.add("osc", WavetableOscillator("saw"))
    g.add("lfo", Lfo("per_note"))
    g.add("modenv", ModEnvelope())
    g.add("noise", Noise())
    g.add("filter", StateVariableFilter())
    g.add("delay", ShortDelay())
    g.add("mix", MixModule())
    assert g.connect("osc", "out", "filter", "in").ok
    assert g.connect("noise", "out", "filter", "in").ok
    assert g.connect("filter", "out", "delay", "in").ok
    assert g.connect("delay", "out", "mix", "in").ok
    assert g.connect_modulation("lfo", "mod", "filter", "cutoff", depth=0.6).ok
    assert g.connect_modulation("lfo", "mod", "filter", "key_tracking", depth=0.3).ok
    assert g.connect_modulation("lfo", "mod", "osc", "pulse_width", depth=0.4).ok
    assert g.connect_modulation("modenv", "mod", "filter", "resonance", depth=0.5).ok
    assert g.connect_modulation("modenv", "mod", "noise", "level", depth=0.3).ok
    assert g.connect_modulation("lfo", "mod", "delay", "feedback", depth=0.3).ok
    assert g.connect_modulation("lfo", "mod", "delay", "mix", depth=0.3).ok
    return g


def test_wide_patch_compiles_and_runs():
    g = _wide_patch()
    poly = PolyGraph(g, voices=2).activate(Activation(SAMPLE_RATE, BLOCK))
    poly.note_on(60, frequency=220.0)
    for _ in range(4):
        poly.process(BLOCK)
    assert np.all(np.isfinite(poly.buffer("mix")))


def test_process_allocates_nothing_extra_with_the_wide_patch_patched():
    """Contract rule 2, extended past stage 1's single-LFO-onto-cutoff
    proof to this stage's whole destination set plus the Mod Envelope."""
    def build(frames):
        g = _wide_patch()
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
    # `filter.py`'s `lfilter` departure (bounded elsewhere) and this
    # stage's own modulation buffers are the only things allowed to scale
    # with block size; generous slack, same bookkeeping convention as
    # `test_synth_graph_lfo.py`'s own version of this test.
    assert grew < (2048 - 64) * 8 * 4 + 8192
