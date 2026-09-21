"""The Level module (#218): a plain gain stage so a feedback loop can be
brought under unity gain, per decision 65 §8's flagged gap.

Same `host()` helper `test_synth_graph_voice.py` uses, repeated rather than
imported for the same reason that file gives: neither file's conventions
should drift into the other's.
"""

import numpy as np
import pytest

from notecolor.audio.graph import contract
from notecolor.audio.graph.contract import Activation, ProcessContext
from notecolor.audio.graph.graph import ModuleGraph
from notecolor.audio.graph.modules.delay import Delay
from notecolor.audio.graph.modules.level import Level
from notecolor.audio.graph.modules.oscillator import WavetableOscillator

SAMPLE_RATE = 44100.0
BLOCK = 512


def host(module, frames=BLOCK, sample_rate=SAMPLE_RATE, note=None):
    module.activate(Activation(sample_rate, frames))
    ports = module.ports()
    inputs = tuple(np.zeros(frames) for p in ports if p.direction == contract.DIRECTION_IN)
    outputs = tuple(np.zeros(frames) for p in ports if p.direction == contract.DIRECTION_OUT)
    return ProcessContext(
        frames=frames, sample_rate=sample_rate, inputs=inputs, outputs=outputs,
        params=module.params.values, note=note,
    )


def test_satisfies_the_contract():
    module = Level()
    assert contract.validate(module) is module
    descriptor = module.descriptor()
    assert descriptor.poly == contract.POLY_EITHER
    # A wire, not a delay: it must not be able to legalise a feedback loop.
    assert descriptor.block_delay == 0


def test_default_is_unity_gain():
    module = Level()
    ctx = host(module)
    ctx.inputs[0][:] = 0.5
    module.process(ctx)
    assert np.allclose(ctx.outputs[0], 0.5)


def test_level_scales_the_signal():
    module = Level()
    ctx = host(module)
    module.params.set("level", 0.25)
    ctx.inputs[0][:] = 1.0
    module.process(ctx)
    assert np.allclose(ctx.outputs[0], 0.25)


def test_negative_level_inverts_rather_than_refusing():
    """Bipolar on purpose (see the module docstring): inverting a repeat
    inside a feedback loop is a real timbral choice, not a mistake to
    guard against."""
    module = Level()
    ctx = host(module)
    module.params.set("level", -1.0)
    ctx.inputs[0][:] = np.linspace(-1.0, 1.0, BLOCK)
    module.process(ctx)
    assert np.allclose(ctx.outputs[0], -ctx.inputs[0])


def test_level_can_bring_a_delay_loop_under_unity_gain():
    """The scenario decision 65 §8 names directly: a unity-gain delay
    (dry + wet) patched back into its own input has loop gain >= 1 the
    moment feedback is above zero. A Level at some value below one in that
    loop is what brings the round-trip gain back under one."""
    module = Level()
    ctx = host(module)
    module.params.set("level", 0.5)
    round_trip_gain = 1.05   # e.g. a delay's dry+wet just over unity
    ctx.inputs[0][:] = round_trip_gain
    module.process(ctx)
    assert float(ctx.outputs[0][0]) * round_trip_gain < round_trip_gain


def test_out_of_range_level_is_clamped_to_the_spec():
    module = Level()
    module.activate(Activation(SAMPLE_RATE, BLOCK))
    module.params.set("level", 10.0)
    assert module.params.get("level") == pytest.approx(2.0)
    module.params.set("level", -10.0)
    assert module.params.get("level") == pytest.approx(-2.0)


SMALL_BLOCK, LARGE_BLOCK = 64, 2048

#: Headroom for `tracemalloc`'s own bookkeeping -- `test_synth_graph_voice.py`
#: uses the same constant for the same reason: measuring the *growth*
#: between two block sizes is what makes a fixed amount of bookkeeping noise
#: wash out, rather than budgeting for it directly.
SLACK_BYTES = 4096


def peak_bytes(module, frames):
    """Peak traced memory over 32 blocks at `frames`, above the steady
    state. Same measurement `test_synth_graph_voice.py`'s allocation tests
    use, repeated here rather than imported so the two files' conventions
    stay independent."""
    import gc
    import tracemalloc

    ctx = ProcessContext(
        frames=frames, sample_rate=SAMPLE_RATE,
        inputs=tuple(np.full(LARGE_BLOCK, 0.5)
                     for p in module.ports() if p.direction == contract.DIRECTION_IN),
        outputs=tuple(np.zeros(LARGE_BLOCK)
                      for p in module.ports() if p.direction == contract.DIRECTION_OUT),
        params=module.params.values, note=None,
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


def test_process_allocates_nothing():
    """No `lfilter` here, no departure to justify: `Level` is a multiply
    into a buffer the host already owns, so its budget is zero."""
    small = Level()
    small.activate(Activation(SAMPLE_RATE, LARGE_BLOCK))
    large = Level()
    large.activate(Activation(SAMPLE_RATE, LARGE_BLOCK))
    grew = peak_bytes(large, LARGE_BLOCK) - peak_bytes(small, SMALL_BLOCK)
    assert grew < SLACK_BYTES


# -- the modulated knob (#208 stage 2 / decision 66) ----------------------
#
# `level` was declared `modulatable=True` from the start and was not read
# that way: `process()` took the smoothed scalar and nothing else, so a
# mod cable onto this knob compiled into a real `ModRoute`, was judged
# ACCEPTED, ran, and produced byte-identical output. These are the tests
# that would have caught it.


def mod_host(module, frames=BLOCK, sample_rate=SAMPLE_RATE, note=None):
    """`host()`, plus the scalar-or-buffer arrays bound the way
    `graph.compile()` binds them -- the same helper
    `test_synth_graph_mod_stage2.py` grew for its own per-destination
    tests."""
    module.activate(Activation(sample_rate, frames))
    ports = module.ports()
    inputs = tuple(np.zeros(frames) for p in ports if p.direction == contract.DIRECTION_IN)
    outputs = tuple(np.zeros(frames) for p in ports if p.direction == contract.DIRECTION_OUT)
    return ProcessContext(
        frames=frames, sample_rate=sample_rate, inputs=inputs, outputs=outputs,
        params=module.params.smoothed, note=note,
        param_buffers=module.params.buffers, param_mod_active=module.params.mod_active,
    )


def test_level_reads_its_modulation_buffer_when_live():
    """Swing the buffer across the knob's declared range and the output has
    to follow it sample by sample, not sit at the scalar."""
    module = Level()
    ctx = mod_host(module)
    ctx.inputs[0][:] = 1.0
    idx = module.params.index("level")
    module.params.set_immediate("level", 1.0)
    module.params.mod_active[idx] = True
    ramp = np.linspace(-2.0, 2.0, BLOCK)
    module.params.buffers[idx][:BLOCK] = ramp
    module.process(ctx)
    assert np.allclose(ctx.outputs[0], ramp)


def test_level_ignores_a_stale_buffer_when_the_knob_is_not_modulated():
    """`mod_active` False means the buffer's contents are not this block's
    story -- the smoothed scalar is."""
    module = Level()
    ctx = mod_host(module)
    ctx.inputs[0][:] = 1.0
    idx = module.params.index("level")
    module.params.set_immediate("level", 0.25)
    module.params.buffers[idx][:BLOCK] = -2.0     # left over, never armed
    module.process(ctx)
    assert np.allclose(ctx.outputs[0], 0.25)


def test_a_modulated_level_still_allocates_nothing():
    """Contract rule 2 on the branch the test above exercises: the buffer
    path must not be the one that allocates."""
    module = Level()
    module.activate(Activation(SAMPLE_RATE, LARGE_BLOCK))
    idx = module.params.index("level")
    module.params.mod_active[idx] = True
    module.params.buffers[idx][:LARGE_BLOCK] = 0.5
    assert peak_bytes(module, LARGE_BLOCK) < SLACK_BYTES


def test_a_zero_length_block_is_a_no_op():
    """The host can hand out a zero-frame block; the neighbouring modules
    guard for it (`noise.py`, `short_delay.py`) and so does this one."""
    module = Level()
    ctx = mod_host(module, frames=BLOCK)
    module.process(ProcessContext(
        frames=0, sample_rate=SAMPLE_RATE, inputs=ctx.inputs, outputs=ctx.outputs,
        params=module.params.smoothed,
        param_buffers=module.params.buffers, param_mod_active=module.params.mod_active,
    ))
    assert np.array_equal(ctx.outputs[0], np.zeros(BLOCK))


# -- inside a real feedback loop -----------------------------------------


def _loop_peaks(level_value, blocks=24):
    """An oscillator into a Delay, the Delay's output folded back into its
    own input through a `Level` -- the patch #218 exists for, built out of
    real modules rather than described. Returns the per-block peak of the
    delay's output.

    Both cables land on the delay's single `in` port, which `compile()`
    sums; the `Delay` in the path is what makes the cycle legal at all
    (`block_delay = 1`, decision 63 §2), and the `Level` is what decides
    whether the round trip is above or below one.
    """
    g = ModuleGraph()
    g.add("osc", WavetableOscillator("sine"))
    g.add("echo", Delay())
    g.add("trim", Level())
    g.activate(Activation(SAMPLE_RATE, BLOCK))

    assert g.connect("osc", "out", "echo", "in").ok
    assert g.connect("echo", "out", "trim", "in").ok
    assert g.connect("trim", "out", "echo", "in").ok      # closes the loop

    g.node("echo").module.params.set("time", BLOCK / SAMPLE_RATE)
    g.node("echo").module.params.set("feedback", 0.0)
    g.node("echo").module.params.set("mix", 1.0)
    g.node("trim").module.params.set("level", level_value)

    compiled = g.compile()
    note = contract.NoteContext(frequency=220.0)
    peaks = []
    for _ in range(blocks):
        compiled.process(BLOCK, note=note)
        peaks.append(float(np.max(np.abs(compiled.buffer("echo")))))
    return peaks


def test_a_level_below_unity_holds_a_real_feedback_loop_bounded():
    """The whole point of the module (decision 65 §8): the same patch that
    builds without limit at unity stays bounded once a Level in the loop
    puts the round trip under one."""
    tamed = _loop_peaks(0.7)
    runaway = _loop_peaks(1.0)
    # Bounded: never more than a small multiple of a single pass.
    assert max(tamed) < 4.0
    # And demonstrably the Level's doing -- the identical patch at unity
    # keeps growing, which nothing refuses and nothing clamps (decision
    # 63 §4).
    assert runaway[-1] > tamed[-1] * 1.5
    assert np.all(np.isfinite(tamed))


def test_a_loop_level_can_be_modulated_without_going_non_finite():
    """A mod cable on the trim knob is a decay the player shapes rather
    than one number -- it must stay finite while it does."""
    module = Level()
    ctx = mod_host(module)
    idx = module.params.index("level")
    module.params.mod_active[idx] = True
    signal = 1.0
    for block in range(64):
        ctx.inputs[0][:] = signal
        # A slow sweep across the attenuating half of the knob.
        module.params.buffers[idx][:BLOCK] = 0.4 + 0.4 * np.sin(block / 5.0)
        module.process(ctx)
        signal = float(np.max(np.abs(ctx.outputs[0])))
        assert np.isfinite(signal)
    assert signal < 1.0
